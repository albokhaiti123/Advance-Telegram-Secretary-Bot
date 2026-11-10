module.exports = {
  apps: [
    {
      name: "telegram-secretary-bot",
      script: "./main.py",
      interpreter: "./venv/bin/python",
      instances: 1, // إلزامي: نسخة واحدة فقط لمنع تضارب جلسة الحساب الشخصي (Telethon)
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_memory_restart: "450M",
      min_uptime: "15s",
      max_restarts: 10,
      restart_delay: 5000,
      kill_timeout: 10000,
      env: {
        PYTHONUNBUFFERED: "1",
        NODE_ENV: "production"
      },
      output: "./logs/pm2_out.log",
      error: "./logs/pm2_error.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    }
  ]
};
