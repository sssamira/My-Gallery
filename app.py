import boto3
import os
from datetime import datetime
from uuid import uuid4

from flask import Flask, redirect, request, jsonify, send_file, abort

app = Flask(__name__, static_folder=".", static_url_path="")

# -----------------------------
# AWS Configuration
# -----------------------------
S3_BUCKET = "amsambucket"          # your S3 bucket name
DYNAMO_TABLE = "GalleryFiles"      # your DynamoDB table name

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)

# -----------------------------
# Storage Service
# -----------------------------
class S3StorageService:
    def save_file(self, file_storage, storage_key):
        """Upload file object to S3"""
        s3.upload_fileobj(file_storage, S3_BUCKET, storage_key)
        return storage_key

    def get_file_url(self, storage_key):
        """Return S3 public URL"""
        return f"https://{S3_BUCKET}.s3.amazonaws.com/{storage_key}"

    def delete_file(self, storage_key):
        """Delete object from S3"""
        s3.delete_object(Bucket=S3_BUCKET, Key=storage_key)

storage_service = S3StorageService()

# -----------------------------
# Helper Function
# -----------------------------
def to_dict(item):
    return {
        "id": item["id"],
        "originalFilename": item["original_filename"],
        "storageKey": item["storage_key"],
        "mimeType": item["mime_type"],
        "sizeBytes": item["size_bytes"],
        "kind": item["kind"],
        "createdAt": item["created_at"],
        "downloadUrl": f"/api/media/{item['id']}/download"
    }

# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def index():
    """Serve the React single-page app"""
    return send_file("index.html")


@app.route("/api/media", methods=["GET"])
def list_media():
    """List all media items"""
    response = table.scan()
    items = sorted(response.get("Items", []), key=lambda x: x["created_at"], reverse=True)
    return jsonify({
        "items": [to_dict(item) for item in items],
        "total": len(items),
        "page": 1,
        "pageSize": len(items)
    })


@app.route("/api/upload", methods=["POST"])
def upload_media():
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

    # Upload to S3
    storage_service.save_file(uploaded_file, storage_key)
    size_bytes = uploaded_file.content_length or 0

    # Save metadata to DynamoDB
    media_item = {
        "id": str(uuid4()),
        "original_filename": original_filename,
        "storage_key": storage_key,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "kind": kind,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    table.put_item(Item=media_item)

    return jsonify(to_dict(media_item)), 201


@app.route("/api/media/<string:media_id>/download", methods=["GET"])
def download_media(media_id):
    """Redirect to S3 URL for download"""
    response = table.get_item(Key={"id": media_id})
    item = response.get("Item")
    if not item:
        abort(404)
    return redirect(storage_service.get_file_url(item["storage_key"]))


@app.route("/api/media/<string:media_id>", methods=["DELETE"])
def delete_media(media_id):
    """Delete media item"""
    response = table.get_item(Key={"id": media_id})
    item = response.get("Item")
    if not item:
        abort(404)

    # Delete from S3
    storage_service.delete_file(item["storage_key"])
    # Delete from DynamoDB
    table.delete_item(Key={"id": media_id})

    return "", 204


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# -----------------------------
# Run App
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)