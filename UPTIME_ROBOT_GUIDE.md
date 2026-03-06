# 🛡️ Guia de Monitoramento 24/7 — UptimeRobot

## 1. Cadastro gratuito
Acesse https://uptimerobot.com e crie conta grátis.

## 2. Criar Monitor
- Clique em **+ Add New Monitor**
- Type: **HTTP(s)**
- Friendly Name: `DANBOT PRO`
- URL: `https://SEU-DOMINIO.railway.app/health`
- Monitoring Interval: **5 minutes**
- Clique em **Create Monitor**

## 3. Alerta por e-mail/Telegram (opcional)
- Em **Alert Contacts**, adicione seu e-mail ou Telegram
- UptimeRobot te avisa em < 5 min se o site cair

## 4. O que o /health retorna
```json
{
  "status": "ok",
  "service": "DANBOT",
  "uptime": "2h 15m",
  "bot_running": true,
  "cpu_pct": 12.3,
  "mem_used_mb": 180,
  "timestamp": "2025-01-01T12:00:00Z"
}
```

## 5. Status interno (apenas logado)
GET /api/watchdog → retorna CPU, RAM, disco, crashes do bot, restarts automáticos
