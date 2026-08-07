from uvicorn.workers import UvicornWorker

from app.config import project_version


class JdoneServerWorker(UvicornWorker):
    def __init__(self, *args, **kwargs):
        self.CONFIG_KWARGS["headers"] = [("server", f"JDONE-Server/{project_version}")]
        super().__init__(*args, **kwargs)
