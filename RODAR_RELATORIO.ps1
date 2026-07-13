# =============================================================================
#  GERADOR DE RELATÓRIO DE DEVOLUÇÕES ML/MP
#  Nautica Refrigeração
#
#  COMO USAR:
#    1. Salve o arquivo after_collection*.xlsx exportado do Mercado Pago
#       na pasta:  C:\Users\Pichau\analise_progress\tmp_csvs\
#
#    2. Clique duplo neste arquivo  OU  rode no PowerShell:
#           .\RODAR_RELATORIO.ps1
#
#  SAÍDA (na pasta reports\):
#    relatorio_devolucoes_YYYY-MM-DD.html  ← abrir no navegador (gráficos interativos)
#    relatorio_devolucoes_YYYY-MM-DD.xlsx  ← abrir no Excel (8 abas)
#    relatorio_devolucoes_YYYY-MM-DD.json  ← dados brutos (opcional)
# =============================================================================

$ErrorActionPreference = "Stop"

# Pasta do projeto
$ROOT = "C:\Users\Pichau\analise_progress"
Set-Location $ROOT

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  RELATÓRIO DE DEVOLUÇÕES ML/MP – Nautica Refrigeração" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Verificar se existe arquivo novo em tmp_csvs
$arquivos = Get-ChildItem "tmp_csvs\after_collection*.xlsx", "tmp_csvs\after_collection*.csv" -ErrorAction SilentlyContinue
if ($arquivos) {
    Write-Host "Arquivos encontrados em tmp_csvs\:" -ForegroundColor Green
    $arquivos | ForEach-Object { Write-Host "  - $($_.Name)" }
} else {
    Write-Host "AVISO: Nenhum arquivo after_collection*.xlsx encontrado em tmp_csvs\" -ForegroundColor Yellow
    Write-Host "Coloque o arquivo exportado do Mercado Pago em tmp_csvs\ e rode novamente." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Pressione Enter para sair"
    exit
}

Write-Host ""
Write-Host "Rodando análise..." -ForegroundColor Cyan

# Rodar o script Python
python -X utf8 scripts\processar_relatorios_mp.py --pasta tmp_csvs --output reports

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERRO ao gerar o relatório. Verifique as mensagens acima." -ForegroundColor Red
    Read-Host "Pressione Enter para sair"
    exit 1
}

# Abrir os arquivos gerados automaticamente
$hoje = Get-Date -Format "yyyy-MM-dd"
$html = "reports\relatorio_devolucoes_$hoje.html"
$xlsx = "reports\relatorio_devolucoes_$hoje.xlsx"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  CONCLUÍDO! Abrindo relatórios..." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

if (Test-Path $html) {
    Write-Host "Abrindo dashboard HTML no navegador..." -ForegroundColor Cyan
    Start-Process $html
    Start-Sleep -Seconds 1
}

if (Test-Path $xlsx) {
    Write-Host "Abrindo XLSX no Excel..." -ForegroundColor Cyan
    Start-Process $xlsx
}

Write-Host ""
Write-Host "Arquivos salvos em:" -ForegroundColor Green
Write-Host "  HTML: $ROOT\$html" 
Write-Host "  XLSX: $ROOT\$xlsx"
Write-Host ""
Read-Host "Pressione Enter para fechar"
