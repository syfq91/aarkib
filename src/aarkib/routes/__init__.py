from aarkib.routes.api import api_bp
from aarkib.routes.auth import auth_bp
from aarkib.routes.opds import opds_bp
from aarkib.routes.reader import reader_bp
from aarkib.routes.ui import ui_bp

__all__ = ["ui_bp", "api_bp", "opds_bp", "reader_bp", "auth_bp"]
