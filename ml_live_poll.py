"""Polling incremental da ML API — mantém o Painel de Devoluções sempre atualizado.
================================================================================
Uso:
    python scripts/ml_live_poll.py                      # ciclo a cada 15 min
    python scripts/ml_live_poll.py --intervalo 5        # a cada 5 min
    python scripts/ml_live_poll.py --serve 8765         # + servidor HTTP local
    python scripts/ml_live_poll.py --once               # um ciclo e sai

Estratégia de performance (polling MÍNIMO):
  - NÃO revalida a base inteira (11k+ pedidos) — só o que pode ter mudado:
      (a) claims ainda abertos no ML (mediations opened)
      (b) pedidos cancelados recentes (--janela-dias, padrão 45)
      (c) pedidos cujo último estado na API não é terminal (pending/erro/None)
  - TTL de 30 min por pedido: nada é re-consultado antes disso.
  - Chamadas em paralelo (--workers, padrão 16) → ciclo típico < 1 min.

Com --serve, o painel é servido em http://localhost:PORTA/painel_devolucoes_live.html
e o navegador mostra o banner "Novos dados disponíveis" quando um ciclo termina
(em file:// o navegador bloqueia o fetch de verificação — use o --serve).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import processar_relatorios_mp as rel
from ml_orders_sync import sync_orders
from src.db.connection import get_db_connection

LIVE_NAME = "painel_devolucoes_live"


def run_cycle(output_dir: Path, janela_dias: int, ttl_min: int, workers: int) -> None:
    """Um ciclo: sync de pedidos novos (API) → revalida estados → regenera o painel."""
    t0 = time.monotonic()
    # 1. pedidos novos/estados de pedido direto da API (fonte primária)
    try:
        from datetime import timedelta, timezone
        desde = (datetime.now(timezone.utc) - timedelta(days=janela_dias)).strftime("%Y-%m-%d")
        sync_orders(desde)
    except Exception as exc:
        print(f"  ⚠ sync orders falhou (segue com a base atual): {type(exc).__name__}: {exc}")

    # 1b. coleta de saldos da página (RPA) — só disputas novas/defasadas (>20h),
    # em lote pequeno para o ciclo continuar rápido; requer sessão salva no perfil
    try:
        import subprocess
        subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "coletar_saldos_meli.py"),
             "--de", desde, "--ate", datetime.now().strftime("%Y-%m-%d"), "--max", "60"],
            timeout=900, check=False,
        )
    except Exception as exc:
        print(f"  ⚠ coleta de saldos falhou (segue): {type(exc).__name__}: {exc}")

    # 1c. estimativas do motor v2 (API pura) para pedidos ainda sem saldo
    # confirmado — NUNCA substitui o saldo coletado, só preenche o "em
    # conciliação" com uma estimativa rotulada enquanto a coleta não chega lá
    try:
        subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "estimar_conciliacao.py"),
             "--max", "150", "--workers", "4"],
            timeout=1200, check=False,
        )
    except Exception as exc:
        print(f"  ⚠ estimativa do motor falhou (segue): {type(exc).__name__}: {exc}")
    conn = get_db_connection()
    try:
        rel._ensure_validation_table(conn)
        ids = rel._select_polling_ids(conn, janela_dias=janela_dias, ttl_min=ttl_min)
        print(f"  {len(ids):,} pedidos candidatos a mudança (claims abertos + cancelados recentes + não-terminais)")

        validation = rel._run_full_validation(conn, n_workers=workers, only_ids=ids)
        df_mp, df_port = rel._load_and_enrich(conn)
    finally:
        conn.close()

    kpi = rel._kpis(df_mp, df_port)

    html_out = output_dir / f"{LIVE_NAME}.html"
    rel._build_html_dashboard(df_mp, df_port, kpi, html_out, validation=validation)

    json_out = output_dir / f"{LIVE_NAME}.json"
    json_out.write_text(json.dumps({
        "gerado_em": datetime.now().isoformat(),
        "kpi": {k: (round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                for k, v in kpi.items()},
        "validacao": {k: v for k, v in validation.items() if k != "detalhes"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # gate de regressão SEMÂNTICA: valida as invariantes de negócio do painel
    # gerado (um-pedido-um-card, grupos×saldo, anomalias, UX sem código cru…)
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "qa_semantica_painel.py"), str(html_out)],
            capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        ultima = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        print(f"  {'✓' if r.returncode == 0 else '✗ REGRESSÃO SEMÂNTICA'} QA semântico: {ultima}")
    except Exception as exc:
        print(f"  ⚠ QA semântico falhou ao rodar: {type(exc).__name__}: {exc}")

    # notificações Slack (#sac): reclamação nova → aviso com prazo; silencioso
    # e inofensivo enquanto o arquivo do webhook não existir
    try:
        r = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "slack_notify.py"), "--once"],
                           capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        ult = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        if ult and "nada a fazer" not in ult:
            print(f"  {ult}")
    except Exception as exc:
        print(f"  ⚠ slack notify falhou (segue): {type(exc).__name__}: {exc}")

    dt = time.monotonic() - t0
    print(f"  ✓ ciclo completo em {dt:,.0f}s — paridade ML API {validation['pct_paridade']}% "
          f"({validation['novos']:,} revalidados agora)")


def _serve(directory: Path, port: int) -> None:
    """Servidor HTTP local (thread daemon) para o painel vivo — sem cache."""
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class NoCacheHandler(SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):
            pass  # não poluir o console do polling

    handler = functools.partial(NoCacheHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"  🌐 Painel vivo: http://localhost:{port}/{LIVE_NAME}.html")


def main() -> None:
    ap = argparse.ArgumentParser(description="Polling incremental ML API → Painel de Devoluções sempre vivo.")
    ap.add_argument("--intervalo", type=float, default=15, help="minutos entre ciclos (padrão: 15)")
    ap.add_argument("--janela-dias", type=int, default=45, dest="janela",
                    help="janela de cancelamentos recentes a vigiar (padrão: 45 dias)")
    ap.add_argument("--ttl-min", type=int, default=30, dest="ttl",
                    help="não re-consultar o mesmo pedido antes de N minutos (padrão: 30)")
    ap.add_argument("--workers", type=int, default=16, help="chamadas paralelas à ML API (padrão: 16)")
    ap.add_argument("--output", default=str(ROOT / "reports"), help="pasta de saída do painel")
    ap.add_argument("--serve", type=int, metavar="PORTA", help="servir a pasta reports/ em http://localhost:PORTA")
    ap.add_argument("--once", action="store_true", help="roda um único ciclo e sai")
    args = ap.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("ML LIVE POLL — painel de devoluções sempre atualizado")
    print(f"intervalo={args.intervalo:g} min | janela={args.janela}d | ttl={args.ttl} min | workers={args.workers}")
    print("=" * 70)

    if args.serve:
        _serve(output_dir, args.serve)

    while True:
        print(f"\n[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] iniciando ciclo…")
        try:
            run_cycle(output_dir, args.janela, args.ttl, args.workers)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # ciclo com erro não derruba o polling — tenta de novo no próximo
            print(f"  ✗ ciclo falhou: {type(exc).__name__}: {exc}")
        if args.once:
            break
        try:
            time.sleep(max(args.intervalo, 0.5) * 60)
        except KeyboardInterrupt:
            print("\nEncerrado pelo usuário.")
            break


if __name__ == "__main__":
    main()
