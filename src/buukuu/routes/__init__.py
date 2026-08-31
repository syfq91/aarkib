from buukuu.routes.api import api_bp
from buukuu.routes.auth import auth_bp
from buukuu.routes.opds import opds_bp
from buukuu.routes.reader import reader_bp
from buukuu.routes.ui import ui_bp

__all__ = ["ui_bp", "api_bp", "opds_bp", "reader_bp", "auth_bp"]
