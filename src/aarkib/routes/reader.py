from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template
from flask.typing import ResponseReturnValue
from flask_login import current_user
from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import MediaItem, UserProgress
from aarkib.routes.auth import require_auth
from aarkib.services.media_service import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS

reader_bp = Blueprint("reader", __name__, url_prefix="/reader")


@reader_bp.route("/item/<int:item_id>")
@reader_bp.route("/media/<int:item_id>")
@require_auth
def open_media_item(item_id: int) -> ResponseReturnValue:
    """Auto-dispatches to the appropriate reader or player view for the given media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Media item not found")
    return redirect(item.player_url)


@reader_bp.route("/epub/<int:item_id>")
@require_auth
def read_epub(item_id: int) -> ResponseReturnValue:
    """Render in-browser EPUB book reader view."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Item not found")
    if item.file_format != "epub":
        abort(400, description="Item is not an EPUB")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )

    return render_template(
        "reader_epub.html",
        item=item,
        book=item,
        initial_location=progress.progress_location if progress else "0",
    )


@reader_bp.route("/cbz/<int:item_id>")
@require_auth
def read_cbz(item_id: int) -> ResponseReturnValue:
    """Render in-browser CBZ comic canvas reader view."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Item not found")
    if item.file_format not in ("cbz", "zip", "cbr"):
        abort(400, description="Item is not a CBZ comic")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )

    initial_page = 1
    if progress and progress.progress_location:
        try:
            initial_page = max(1, int(float(progress.progress_location)))
        except ValueError:
            initial_page = 1

    return render_template(
        "reader_cbz.html",
        item=item,
        book=item,
        initial_page=initial_page,
    )


@reader_bp.route("/pdf/<int:item_id>")
@require_auth
def read_pdf(item_id: int) -> ResponseReturnValue:
    """Render in-browser PDF document reader view."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Item not found")
    if item.file_format != "pdf":
        abort(400, description="Item is not a PDF")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )

    initial_page = 1
    if progress and progress.progress_location:
        try:
            initial_page = max(1, int(float(progress.progress_location)))
        except ValueError, TypeError:
            initial_page = 1

    return render_template(
        "reader_pdf.html",
        item=item,
        book=item,
        initial_page=initial_page,
        total_pages=item.page_count or 1,
    )


@reader_bp.route("/video/<int:item_id>")
@require_auth
def watch_video(item_id: int) -> ResponseReturnValue:
    """Render in-browser video player view with seek and resume support."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Video not found")
    if not item.is_video and item.file_format not in VIDEO_EXTENSIONS:
        abort(400, description="Item is not a video")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )

    initial_time = 0.0
    if progress and progress.progress_location:
        try:
            initial_time = max(0.0, float(progress.progress_location))
        except ValueError, TypeError:
            initial_time = 0.0

    # Next / previous episode navigation if part of a collection / show
    next_video = None
    prev_video = None
    if item.collection_id:
        episodes = db.session.scalars(
            select(MediaItem)
            .where(MediaItem.collection_id == item.collection_id)
            .order_by(MediaItem.series_index.asc(), MediaItem.id.asc())
        ).all()
        for idx, ep in enumerate(episodes):
            if ep.id == item.id:
                if idx + 1 < len(episodes):
                    next_video = episodes[idx + 1]
                if idx > 0:
                    prev_video = episodes[idx - 1]
                break

    return render_template(
        "reader_video.html",
        item=item,
        book=item,
        initial_time=initial_time,
        progress=progress,
        next_video=next_video,
        prev_video=prev_video,
    )


@reader_bp.route("/audiobook/<int:item_id>")
@require_auth
def play_audiobook(item_id: int) -> ResponseReturnValue:
    """Dedicated audiobook web player with chapter selection, sleep timer, and speed controls."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Audiobook not found")
    if not item.is_audiobook and not item.is_audio and item.file_format != "m4b":
        abort(400, description="Item is not an audiobook")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )

    initial_time = 0.0
    if progress and progress.progress_location:
        try:
            initial_time = max(0.0, float(progress.progress_location))
        except ValueError, TypeError:
            initial_time = 0.0

    next_track = None
    prev_track = None
    if item.collection_id:
        tracks = db.session.scalars(
            select(MediaItem)
            .where(MediaItem.collection_id == item.collection_id)
            .order_by(MediaItem.series_index.asc(), MediaItem.id.asc())
        ).all()
        for idx, trk in enumerate(tracks):
            if trk.id == item.id:
                if idx + 1 < len(tracks):
                    next_track = tracks[idx + 1]
                if idx > 0:
                    prev_track = tracks[idx - 1]
                break

    return render_template(
        "player_audiobook.html",
        item=item,
        initial_time=initial_time,
        progress=progress,
        chapters=item.chapters,
        next_track=next_track,
        prev_track=prev_track,
    )


@reader_bp.route("/music/<int:item_id>")
@require_auth
def play_music(item_id: int) -> ResponseReturnValue:
    """Dedicated music track player."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Music item not found")
    if not item.is_audio and item.file_format not in AUDIO_EXTENSIONS:
        abort(400, description="Item is not an audio track")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )

    initial_time = 0.0
    if progress and progress.progress_location:
        try:
            initial_time = max(0.0, float(progress.progress_location))
        except ValueError, TypeError:
            initial_time = 0.0

    next_track = None
    prev_track = None
    if item.collection_id:
        tracks = db.session.scalars(
            select(MediaItem)
            .where(MediaItem.collection_id == item.collection_id)
            .order_by(MediaItem.series_index.asc(), MediaItem.id.asc())
        ).all()
        for idx, trk in enumerate(tracks):
            if trk.id == item.id:
                if idx + 1 < len(tracks):
                    next_track = tracks[idx + 1]
                if idx > 0:
                    prev_track = tracks[idx - 1]
                break

    return render_template(
        "player_audio.html",
        item=item,
        initial_time=initial_time,
        progress=progress,
        next_track=next_track,
        prev_track=prev_track,
    )


@reader_bp.route("/podcast/<int:item_id>")
@require_auth
def play_podcast(item_id: int) -> ResponseReturnValue:
    """Dedicated podcast episode player with episode notes and jump navigation."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Podcast episode not found")
    if not item.is_audio and item.file_format not in AUDIO_EXTENSIONS:
        abort(400, description="Item is not an audio episode")

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )
    progress = db.session.scalar(
        select(UserProgress).where(
            user_cond,
            UserProgress.media_item_id == item.id,
        )
    )

    initial_time = 0.0
    if progress and progress.progress_location:
        try:
            initial_time = max(0.0, float(progress.progress_location))
        except ValueError, TypeError:
            initial_time = 0.0

    next_episode = None
    prev_episode = None
    all_episodes: list[MediaItem] = []

    if item.collection_id:
        all_episodes = list(
            db.session.scalars(
                select(MediaItem)
                .where(MediaItem.collection_id == item.collection_id)
                .order_by(MediaItem.series_index.asc(), MediaItem.id.asc())
            ).all()
        )
        for idx, ep in enumerate(all_episodes):
            if ep.id == item.id:
                if idx + 1 < len(all_episodes):
                    next_episode = all_episodes[idx + 1]
                if idx > 0:
                    prev_episode = all_episodes[idx - 1]
                break

    return render_template(
        "player_podcast.html",
        item=item,
        initial_time=initial_time,
        progress=progress,
        next_episode=next_episode,
        prev_episode=prev_episode,
        all_episodes=all_episodes,
    )


@reader_bp.route("/audio/<int:item_id>")
@require_auth
def play_audio(item_id: int) -> ResponseReturnValue:
    """Unified audio playback dispatching to dedicated audiobook player or music player."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Audio item not found")
    if not item.is_audio and item.file_format not in AUDIO_EXTENSIONS:
        abort(400, description="Item is not an audio track")

    if item.is_audiobook:
        return play_audiobook(item_id)
    if item.is_podcast:
        return play_podcast(item_id)

    return play_music(item_id)
