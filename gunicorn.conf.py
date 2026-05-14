import os

# Render sets PORT env var
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 2
threads  = 2
timeout  = 120
loglevel = "info"
accesslog = "-"   # stdout
errorlog  = "-"   # stderr
