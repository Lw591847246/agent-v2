# app/main.py
from app.bootstrap import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    from app.config.settings import server_settings
    uvicorn.run(
        "app.main:app",
        host=server_settings.host,
        port=server_settings.port,
        workers=server_settings.workers,
    )