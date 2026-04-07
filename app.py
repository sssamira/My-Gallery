import os
from datetime import datetime
from uuid import uuid4

from flask import Flask, request, jsonify, send_file, abort
from flask_sqlalchemy import SQLAlchemy


app = Flask(__name__, static_folder=".", static_url_path="")

# Database configuration
os.makedirs(app.instance_path, exist_ok=True)

def _default_db_url() -> str:
    db_path = os.path.join(app.instance_path, "gallery.db")
    return f"sqlite:///{db_path}"

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DB_URL", _default_db_url())
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


db = SQLAlchemy(app)


class Media(db.Model):  # type: ignore[name-defined]
    __tablename__ = "media"

    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    storage_key = db.Column(db.String(512), unique=True, nullable=False)
    mime_type = db.Column(db.String(255), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(32), nullable=False)  # image, video, proto
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "originalFilename": self.original_filename,
            "storageKey": self.storage_key,
            "mimeType": self.mime_type,
            "sizeBytes": self.size_bytes,
            "kind": self.kind,
            "createdAt": self.created_at.isoformat() + "Z",
            "downloadUrl": f"/api/media/{self.id}/download",
        }


class LocalStorageService:
    def __init__(self, root_dir: str) -> None:
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)

    def _full_path(self, storage_key: str) -> str:
        path = os.path.join(self.root_dir, storage_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def save_file(self, file_storage, storage_key: str) -> str:
        path = self._full_path(storage_key)
        file_storage.save(path)
        return storage_key

    def get_path(self, storage_key: str) -> str:
        path = os.path.join(self.root_dir, storage_key)
        if not os.path.isfile(path):
            raise FileNotFoundError(storage_key)
        return path

    def delete_file(self, storage_key: str) -> None:
        try:
            path = self.get_path(storage_key)
        except FileNotFoundError:
            return
        try:
            os.remove(path)
        except OSError:
            pass


STORAGE_ROOT = os.getenv("STORAGE_ROOT", os.path.join(app.instance_path, "uploads"))
storage_service = LocalStorageService(STORAGE_ROOT)


with app.app_context():
    db.create_all()


@app.route("/")
def index() -> object:
    """Serve the React single-page app."""
    return send_file("index.html")


@app.route("/api/media", methods=["GET"])
def list_media() -> object:
    page = max(int(request.args.get("page", 1)), 1)
    page_size = max(min(int(request.args.get("pageSize", 24)), 100), 1)

    query = Media.query.order_by(Media.created_at.desc())
    total = query.count()
    items = (
        query.offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return jsonify(
        {
            "items": [m.to_dict() for m in items],
            "page": page,
            "pageSize": page_size,
            "total": total,
        }
    )


@app.route("/api/upload", methods=["POST"])
def upload_media() -> object:
    uploaded_file = request.files.get("file")
    kind = (request.form.get("kind") or "").lower() or "other"

    if uploaded_file is None or uploaded_file.filename == "":
        return jsonify({"error": "No file provided"}), 400

    if kind not in {"image", "video", "proto", "other"}:
        return jsonify({"error": "Invalid kind"}), 400

    original_filename = uploaded_file.filename
    mime_type = uploaded_file.mimetype or "application/octet-stream"

    ext = os.path.splitext(original_filename)[1]
    storage_key = os.path.join(kind, f"{uuid4().hex}{ext}")

    storage_service.save_file(uploaded_file, storage_key)
    full_path = storage_service.get_path(storage_key)
    size_bytes = os.path.getsize(full_path)

    media = Media(
        original_filename=original_filename,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size_bytes,
        kind=kind,
    )
    db.session.add(media)
    db.session.commit()

    return jsonify(media.to_dict()), 201


@app.route("/api/media/<int:media_id>/download", methods=["GET"])
def download_media(media_id: int) -> object:
    media = Media.query.get(media_id)
    if media is None:
        abort(404)

    try:
        path = storage_service.get_path(media.storage_key)
    except FileNotFoundError:
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=media.original_filename,
        mimetype=media.mime_type,
    )


@app.route("/api/media/<int:media_id>", methods=["DELETE"])
def delete_media(media_id: int) -> object:
    media = Media.query.get(media_id)
    if media is None:
        abort(404)

    storage_service.delete_file(media.storage_key)
    db.session.delete(media)
    db.session.commit()

    return ("", 204)


@app.route("/health", methods=["GET"])
def health() -> object:
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
