from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import click
from flask import Flask
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import Engine

from aarkib.config import Config
from aarkib.extensions import db, login_manager
from aarkib.routes import api_bp, auth_bp, opds_bp, reader_bp, ui_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aarkib")


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def migrate_database() -> None:
    """Ensure database tables and columns match the declared SQLAlchemy models."""
    db.create_all()
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    with db.engine.begin() as conn:
        for table_name, table in db.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    col_type = col.type.compile(conn.dialect)
                    stmt = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"
                    logger.info(
                        "Auto-migrating %s: added missing column %s (%s)",
                        table_name,
                        col.name,
                        col_type,
                    )
                    conn.execute(text(stmt))


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.from_object(config_class)

    # Ensure required data directories exist
    data_dir = Path(app.config.get("DATA_DIR", "data"))
    covers_dir = Path(app.config.get("COVERS_DIR", data_dir / "covers"))
    optimized_dir = Path(app.config.get("OPTIMIZED_DIR", data_dir / "optimized"))
    data_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    optimized_dir.mkdir(parents=True, exist_ok=True)

    from aarkib.services.scanner import get_library_dirs

    for lib_dir in get_library_dirs(app):
        lib_dir.mkdir(parents=True, exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)

    # Initialize media plugins
    from aarkib.plugins import init_plugins

    init_plugins(app)

    # Register blueprints
    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(opds_bp)
    if "reader" not in app.blueprints:
        app.register_blueprint(reader_bp)
    app.register_blueprint(auth_bp)

    # Security settings & headers
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    with app.app_context():
        migrate_database()

    # Register CLI commands
    register_commands(app)

    return app


def register_commands(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command():
        """Initialize the database tables."""
        migrate_database()
        click.echo("Initialized the database.")

    @app.cli.command("scan")
    def scan_command():
        """Scan the library directory for books."""
        from aarkib.services.scanner import scan_library

        click.echo("Scanning library...")
        result = scan_library(app)
        click.echo(f"Scan complete: {result}")

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin_command(username, password):
        """Create an administrator user."""
        from aarkib.models import User

        user = db.session.scalar(select(User).where(User.username == username))
        if user:
            user.set_password(password)
            user.is_admin = True
            click.echo(f"Updated password for admin user: {username}")
        else:
            user = User(username=username, is_admin=True)
            user.set_password(password)
            db.session.add(user)
            click.echo(f"Created admin user: {username}")
        db.session.commit()

    @app.cli.command("create-user")
    @click.argument("username")
    @click.password_option()
    @click.option("--admin", is_flag=True, help="Grant admin privileges")
    def create_user_command(username, password, admin):
        """Create a standard or admin user."""
        from aarkib.models import User

        existing = db.session.scalar(select(User).where(User.username == username))
        if existing:
            click.echo(f"Error: User '{username}' already exists.")
            return
        user = User(username=username, is_admin=admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created user: {username} ({'Admin' if admin else 'Reader'})")

    @app.cli.command("list-users")
    def list_users_command():
        """List all registered users."""
        from aarkib.models import User

        users = db.session.scalars(select(User).order_by(User.id.asc())).all()
        if not users:
            click.echo("No users registered.")
            return
        click.echo(f"{'ID':<4} {'Username':<20} {'Role':<10} {'Joined':<12}")
        click.echo("-" * 48)
        for u in users:
            role = "Admin" if u.is_admin else "Reader"
            joined = u.created_at.strftime("%Y-%m-%d") if u.created_at else "N/A"
            click.echo(f"{u.id:<4} {u.username:<20} {role:<10} {joined:<12}")

    @app.cli.command("enrich")
    @click.option("--book-id", type=int, help="ID of specific book to enrich")
    @click.option("--overwrite", is_flag=True, help="Overwrite existing metadata")
    @click.option(
        "--provider",
        default="all",
        type=click.Choice(["all", "googlebooks", "openlibrary"]),
        help="Metadata provider",
    )
    def enrich_command(book_id, overwrite, provider):
        """Enrich book metadata using online sources."""
        from aarkib.models import Book
        from aarkib.services.enricher import enrich_all_books, enrich_book

        covers_dir = Path(app.config["COVERS_DIR"])
        if book_id:
            book = db.session.get(Book, book_id)
            if not book:
                click.echo(f"Book ID {book_id} not found.")
                return
            click.echo(f"Enriching '{book.title}'...")
            res = enrich_book(book, covers_dir, overwrite=overwrite, provider=provider)
            click.echo(f"Result: {res}")
        else:
            click.echo("Enriching all books in library...")
            res = enrich_all_books(app, overwrite=overwrite, provider=provider)
            click.echo(f"Enrichment complete: {res}")


def main() -> None:
    """CLI entry point for running the server or administration commands."""
    import sys

    from flask.cli import ScriptInfo

    app = create_app()

    if len(sys.argv) > 1:
        app.cli.main(
            args=sys.argv[1:],
            prog_name="aarkib",
            obj=ScriptInfo(create_app=lambda: app),
        )
        return

    from aarkib.services.scanner import scan_library, start_library_watcher

    if app.config.get("AUTO_SCAN_ON_START", True):
        try:
            scan_library(app)
        except Exception as e:
            logger.warning("Initial scan error: %s", e)

    if app.config.get("WATCH_LIBRARY", True):
        try:
            start_library_watcher(app)
        except Exception as e:
            logger.warning("Library watcher error: %s", e)

    import os

    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
