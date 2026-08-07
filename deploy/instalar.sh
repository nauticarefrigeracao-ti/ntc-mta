#!/usr/bin/env bash
# Instala o listener do SAC num VPS Debian/Ubuntu limpo.
#
# Idempotente: rodar de novo atualiza o codigo e reinicia o servico, sem
# duplicar nada. E o mesmo comando para instalar e para atualizar.
#
#   sudo bash instalar.sh
#
# Depois de instalar, PREENCHA /etc/ntc-sac/ambiente com os tokens e rode:
#   sudo systemctl restart ntc-sac-listener
set -euo pipefail

REPO="https://github.com/nauticarefrigeracao-ti/ntc-mta.git"
DESTINO="/opt/ntc-mta"
DADOS="/var/lib/ntc-sac"
CONFIG="/etc/ntc-sac"
USUARIO="ntcsac"

[ "$EUID" -eq 0 ] || { echo "rode com sudo"; exit 1; }

echo "==> pacotes"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

echo "==> usuario $USUARIO (sem shell, sem home -- so roda o servico)"
id -u "$USUARIO" &>/dev/null || useradd --system --shell /usr/sbin/nologin "$USUARIO"

echo "==> codigo em $DESTINO"
if [ -d "$DESTINO/.git" ]; then
    git -C "$DESTINO" fetch --quiet origin main
    git -C "$DESTINO" reset --hard --quiet origin/main
else
    git clone --quiet "$REPO" "$DESTINO"
fi

echo "==> dependencias"
python3 -m venv "$DESTINO/.venv" 2>/dev/null || true
"$DESTINO/.venv/bin/pip" install --quiet --upgrade pip
"$DESTINO/.venv/bin/pip" install --quiet psycopg2-binary websockets

echo "==> pastas"
mkdir -p "$DADOS" "$CONFIG"
chown -R "$USUARIO":"$USUARIO" "$DADOS" "$DESTINO"

if [ ! -f "$CONFIG/ambiente" ]; then
    cat > "$CONFIG/ambiente" <<'AMBIENTE'
# Preencha e NAO commite. Uma linha por variavel, sem aspas.
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ML_NEON_URL=postgresql://...
SAC_PULSO_ARQUIVO=/var/lib/ntc-sac/pulso.jsonl
AMBIENTE
    echo "    criado $CONFIG/ambiente -- PREENCHA OS TOKENS"
fi
# Segredo so para root. O servico le como ntcsac via EnvironmentFile, que o
# systemd carrega ANTES de baixar privilegio.
chmod 600 "$CONFIG/ambiente"

echo "==> servicos"
install -m 644 "$DESTINO/deploy/ntc-sac-listener.service" /etc/systemd/system/
install -m 644 "$DESTINO/deploy/ntc-sac-saude.service"    /etc/systemd/system/
install -m 644 "$DESTINO/deploy/ntc-sac-saude.timer"      /etc/systemd/system/
systemctl daemon-reload

# `enable` e o que faz sobreviver a reboot -- sem ele, uma queda de energia
# deixa o SAC morto ate alguem entrar na maquina.
systemctl enable --now ntc-sac-listener.service
systemctl enable --now ntc-sac-saude.timer

echo
echo "==> pronto. Conferir:"
echo "    systemctl status ntc-sac-listener"
echo "    journalctl -u ntc-sac-listener -n 30 --no-pager"
