from flask import Flask, send_from_directory, jsonify, render_template, send_file, request, session, redirect, url_for
import os
from io import BytesIO
import zipfile
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from functools import wraps
import uuid
import requests
import cloudinary
import cloudinary.uploader
import cloudinary.api
from dotenv import load_dotenv
from datetime import datetime
import json
import time

# Load environment variables
load_dotenv()

# Initialize Flask app with static/template folders
app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.urandom(24)  # Session encryption key

# Configure Cloudinary
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
    secure=True
)

# Define folder in Cloudinary for our images
CLOUDINARY_FOLDER = "whiteboard_captures"
# Define alternative folders to check for backward compatibility
ALTERNATIVE_FOLDERS = ["whiteboard_images"]  # Add any other folders that might contain images

# Keep local directory for backward compatibility during transition
IMAGE_DIR = "D:/test/images"  # Local image storage (will be phased out)
WHITEBOARD_SIZE = (864, 576)  # PDF page size (12x8 inches at 72 DPI)
user_deleted_images = {}  # Track deletions per user session
user_session_start_times = {}  # Track when each user session started

# Metadata file to store image timestamps (since Cloudinary doesn't preserve original timestamps)
METADATA_FILE = "image_metadata.json"

# Load or create metadata file
def load_metadata():
    try:
        if os.path.exists(METADATA_FILE):
            with open(METADATA_FILE, 'r') as f:
                return json.load(f)
        else:
            return {}
    except Exception as e:
        print(f"Error loading metadata: {e}")
        return {}

# Save metadata
def save_metadata(metadata):
    try:
        with open(METADATA_FILE, 'w') as f:
            json.dump(metadata, f)
    except Exception as e:
        print(f"Error saving metadata: {e}")

# Initialize or load image metadata
image_metadata = load_metadata()

# Assign a unique session ID on first request
@app.before_request
def init_user_session():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        user_deleted_images[session['user_id']] = set()
        # Record session start time for filtering images
        user_session_start_times[session['user_id']] = int(time.time())

# Serve the main page
@app.route('/')
def index():
    return render_template('index.html')  # Renders index.html

# Test route to check Cloudinary connection
@app.route('/test-cloudinary')
def test_cloudinary():
    try:
        # Get resource types
        resource_types = cloudinary.api.resource_types()
        
        # Also test with a simple ping-like call
        usage_info = cloudinary.api.usage()
        
        # Test if our folder exists
        try:
            folder_info = cloudinary.api.resources(prefix=f"{CLOUDINARY_FOLDER}/", type="upload")
            folder_exists = True
        except Exception:
            folder_exists = False
        
        return jsonify({
            "message": "Cloudinary connection successful", 
            "resource_types": resource_types,
            "usage_info": usage_info,
            "folder_exists": folder_exists,
            "folder_path": CLOUDINARY_FOLDER
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/test-cloudinary-page')
def test_cloudinary_page():
    return render_template('test_cloudinary.html')

# Test upload form
@app.route('/test-upload')
def test_upload_form():
    return render_template('test_upload.html')

# Helper function to synchronize Cloudinary images with local metadata
def sync_cloudinary_images():
    # Add any new images from Cloudinary to our metadata
    try:
        # First check our main folder
        fetch_cloudinary_folder_images(CLOUDINARY_FOLDER)
        
        # Then check alternative folders if defined
        for alt_folder in ALTERNATIVE_FOLDERS:
            fetch_cloudinary_folder_images(alt_folder)
        
        # Save updated metadata
        save_metadata(image_metadata)
        return True
    except Exception as e:
        print(f"Error synchronizing Cloudinary images: {e}")
        return False

def fetch_cloudinary_folder_images(folder_name):
    try:
        # Get images from the specified Cloudinary folder with higher max_results
        results = cloudinary.api.resources(
            type="upload",
            prefix=f"{folder_name}/",
            max_results=500  # Increased from default to get more images
        )
        
        # Process each resource
        for resource in results.get('resources', []):
            public_id = resource['public_id']
            format = resource.get('format', 'png')  # Default to png if format is not available
            
            # Generate filename from public_id
            base_filename = os.path.basename(public_id)
            
            # Check if this is already in our metadata by cloudinary_id
            existing_entry = False
            for key, data in image_metadata.items():
                if data.get('cloudinary_id') == public_id:
                    existing_entry = True
                    break
            
            # If not in metadata, add it
            if not existing_entry:
                # Extract timestamp from filename or use upload date
                if 'whiteboard_' in base_filename and base_filename.split('whiteboard_')[1].split('.')[0].isdigit():
                    timestamp = int(base_filename.split('whiteboard_')[1].split('.')[0])
                else:
                    # Use the upload timestamp from Cloudinary
                    created_at = resource.get('created_at', '')
                    if created_at:
                        # Parse Cloudinary datetime format
                        try:
                            dt = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%SZ')
                            timestamp = int(dt.timestamp())
                        except:
                            timestamp = int(time.time())
                    else:
                        timestamp = int(time.time())
                
                # Create a standardized filename
                filename = f"whiteboard_{timestamp}.{format}"
                
                # Add to metadata
                image_metadata[filename] = {
                    "timestamp": timestamp,
                    "cloudinary_id": public_id,
                    "url": resource['secure_url']
                }
        
        # Handle pagination if needed
        if 'next_cursor' in results:
            try:
                # Get next page of results using cursor
                next_results = cloudinary.api.resources(
                    type="upload",
                    prefix=f"{folder_name}/",
                    max_results=500,
                    next_cursor=results['next_cursor']
                )
                
                # Process additional resources
                for resource in next_results.get('resources', []):
                    public_id = resource['public_id']
                    format = resource.get('format', 'png')
                    
                    # Check if already in metadata
                    existing_entry = False
                    for key, data in image_metadata.items():
                        if data.get('cloudinary_id') == public_id:
                            existing_entry = True
                            break
                    
                    if not existing_entry:
                        # Extract timestamp or use upload date
                        base_filename = os.path.basename(public_id)
                        if 'whiteboard_' in base_filename and base_filename.split('whiteboard_')[1].split('.')[0].isdigit():
                            timestamp = int(base_filename.split('whiteboard_')[1].split('.')[0])
                        else:
                            created_at = resource.get('created_at', '')
                            if created_at:
                                try:
                                    dt = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%SZ')
                                    timestamp = int(dt.timestamp())
                                except:
                                    timestamp = int(time.time())
                            else:
                                timestamp = int(time.time())
                        
                        filename = f"whiteboard_{timestamp}.{format}"
                        image_metadata[filename] = {
                            "timestamp": timestamp,
                            "cloudinary_id": public_id,
                            "url": resource['secure_url']
                        }
            except Exception as e:
                print(f"Error fetching additional images from Cloudinary folder {folder_name}: {e}")
    except Exception as e:
        print(f"Error fetching images from Cloudinary folder {folder_name}: {e}")

# API: Upload image to Cloudinary
@app.route('/api/upload', methods=['POST'])
def upload_image():
    if 'image' not in request.files:
        return jsonify({"error": "No image provided"}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    try:
        # Generate a unique filename based on timestamp
        timestamp = int(time.time())
        extension = os.path.splitext(file.filename)[1].lower()
        base_filename = f"whiteboard_{timestamp}"
        filename = f"{base_filename}{extension}"
        
        # Upload to Cloudinary with explicit folder path
        result = cloudinary.uploader.upload(
            file,
            public_id=base_filename,  # Just the filename, no path
            folder=CLOUDINARY_FOLDER,  # Explicitly specify the folder
            resource_type="image",
            use_filename=False  # Don't use original filename
        )
        
        # Store metadata for sorting by timestamp later
        image_metadata[filename] = {
            "timestamp": timestamp,
            "cloudinary_id": result['public_id'],
            "url": result['secure_url']
        }
        save_metadata(image_metadata)
        
        return jsonify({
            "message": "Upload successful",
            "filename": filename,
            "cloudinary_id": result['public_id'],
            "url": result['secure_url'],
            "folder": CLOUDINARY_FOLDER
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# API: Delete an image for the current user
@app.route('/api/delete/<filename>', methods=['DELETE'])
def delete_image(filename):
    user_id = session.get('user_id')
    if not user_id: return jsonify({"error": "No session"}), 401  # Unauthorized
    
    # Check if image exists in metadata
    if filename in image_metadata:
        try:
            # Delete from Cloudinary too
            cloudinary_id = image_metadata[filename]['cloudinary_id']
            cloudinary.uploader.destroy(cloudinary_id)
            # Remove from metadata
            del image_metadata[filename]
            save_metadata(image_metadata)
        except Exception as e:
            print(f"Error deleting from Cloudinary: {e}")
    
    # Add to user's deleted images list (for session)
    user_deleted_images[user_id].add(filename)
    
    return jsonify({"message": "Deleted"})

# API: Force synchronization with Cloudinary
@app.route('/api/sync-cloudinary')
def api_sync_cloudinary():
    try:
        success = sync_cloudinary_images()
        if success:
            return jsonify({"message": "Successfully synchronized with Cloudinary", "image_count": len(image_metadata)})
        else:
            return jsonify({"error": "Synchronization failed"}), 500
    except Exception as e:
        return jsonify({"error": f"Error: {str(e)}"}), 500

# API: List non-deleted images for the current user (sorted by timestamp)
@app.route('/api/images')
def list_images():
    user_id = session.get('user_id')
    if not user_id: return jsonify({"error": "No session"}), 401

    # First sync with Cloudinary to ensure we have the latest images
    sync_cloudinary_images()
    
    # Get session start time for filtering
    session_start_time = user_session_start_times.get(user_id, 0)
    
    # Now use the metadata to list images
    image_list = []
    for filename, data in image_metadata.items():
        # Only include images that are:
        # 1. Not deleted by this user AND
        # 2. Uploaded after this session started
        if (filename not in user_deleted_images.get(user_id, set()) and 
            data["timestamp"] >= session_start_time):
            image_list.append({
                "filename": filename,
                "timestamp": data["timestamp"],
                "cloudinary_url": data["url"]
            })
    
    # Sort by timestamp ascending (oldest -> newest)
    sorted_images = sorted(image_list, key=lambda x: x['timestamp'])
    
    return jsonify(sorted_images)

# API: Serve an image (block access to deleted ones)
@app.route('/api/images/<filename>')
def get_image(filename):
    user_id = session.get('user_id')
    if not user_id: return jsonify({"error": "No session"}), 401
    if filename in user_deleted_images[user_id]: return jsonify({"error": "Not available"}), 404
    
    # Try to get from Cloudinary first
    if filename in image_metadata:
        # Redirect to Cloudinary URL
        return redirect(image_metadata[filename]["url"])
    else:
        # Try to sync first to see if it's a newly uploaded image
        sync_cloudinary_images()
        if filename in image_metadata:
            return redirect(image_metadata[filename]["url"])
        
        # Fallback to local file
        return send_from_directory(IMAGE_DIR, filename)

# API: Download all non-deleted images as ZIP
@app.route('/api/download')
def download_all():
    user_id = session.get('user_id')
    if not user_id: return jsonify({"error": "No session"}), 401
    
    # Sync with Cloudinary first
    sync_cloudinary_images()
    
    # Get session start time for filtering
    session_start_time = user_session_start_times.get(user_id, 0)
    
    # Create ZIP with images in the correct order
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        # Use Cloudinary images
        image_list = []
        for filename, data in image_metadata.items():
            if (filename not in user_deleted_images.get(user_id, set()) and 
                data["timestamp"] >= session_start_time):
                image_list.append({
                    "filename": filename,
                    "timestamp": data["timestamp"],
                    "url": data["url"]
                })
        
        # Sort by timestamp ascending (oldest -> newest)
        sorted_images = sorted(image_list, key=lambda x: x['timestamp'])
        
        # Download each image from Cloudinary and add to ZIP
        for img in sorted_images:
            response = requests.get(img['url'])
            if response.status_code == 200:
                zip_file.writestr(img['filename'], response.content)
            
    buffer.seek(0)
    return send_file(buffer, mimetype='application/zip', as_attachment=True, download_name='images.zip')

# API: Generate a PDF with all non-deleted images (one per page)
@app.route('/api/download-pdf')
def download_pdf():
    user_id = session.get('user_id')
    if not user_id: return jsonify({"error": "No session"}), 401
    
    # Sync with Cloudinary first
    sync_cloudinary_images()
    
    # Get session start time for filtering
    session_start_time = user_session_start_times.get(user_id, 0)
    
    # Generate PDF with images in the correct order
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=WHITEBOARD_SIZE)
    
    # Use Cloudinary images
    image_list = []
    for filename, data in image_metadata.items():
        if (filename not in user_deleted_images.get(user_id, set()) and 
            data["timestamp"] >= session_start_time):
            image_list.append({
                "filename": filename,
                "timestamp": data["timestamp"],
                "url": data["url"]
            })
    
    # Sort by timestamp ascending (oldest -> newest)
    sorted_images = sorted(image_list, key=lambda x: x['timestamp'])
    
    # Download each image from Cloudinary and add to PDF
    for img in sorted_images:
        response = requests.get(img['url'])
        if response.status_code == 200:
            img_file = BytesIO(response.content)
            pdf.drawImage(ImageReader(img_file), 0, 0, *WHITEBOARD_SIZE)
            pdf.showPage()  # New page
    
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name='images.pdf')

# Route to create the Cloudinary folder if it doesn't exist
@app.route('/api/setup-folder')
def setup_folder():
    try:
        # Create a dummy file to ensure the folder exists
        # We use a 1x1 transparent pixel
        result = cloudinary.uploader.upload(
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
            public_id="folder_placeholder",  # Just the filename, no path
            folder=CLOUDINARY_FOLDER,  # Specify folder explicitly
            resource_type="image"
        )
        
        # Verify folder exists by listing its contents
        try:
            resources = cloudinary.api.resources(
                type="upload",
                prefix=f"{CLOUDINARY_FOLDER}/",
                max_results=1
            )
            folder_exists = len(resources.get('resources', [])) > 0
        except Exception as folder_e:
            folder_exists = False
            
        return jsonify({
            "message": f"Folder '{CLOUDINARY_FOLDER}' created or confirmed", 
            "result": result,
            "folder_exists": folder_exists
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Route to reset user's session (clears image history)
@app.route('/api/reset-session')
def reset_session():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "No session"}), 401
    
    # Update session start time to now
    user_session_start_times[user_id] = int(time.time())
    # Clear any deleted images for this session
    user_deleted_images[user_id] = set()
    
    return jsonify({"message": "Session reset successful", "new_start_time": user_session_start_times[user_id]})

# Simple status endpoint
@app.route('/api/status')
def status():
    # Sync with Cloudinary first to get accurate count
    sync_cloudinary_images()
    
    # Count images by session
    user_id = session.get('user_id')
    session_image_count = 0
    if user_id:
        session_start_time = user_session_start_times.get(user_id, 0)
        for filename, data in image_metadata.items():
            if (filename not in user_deleted_images.get(user_id, set()) and 
                data["timestamp"] >= session_start_time):
                session_image_count += 1
    
    return jsonify({
        "status": "online",
        "cloudinary_folder": CLOUDINARY_FOLDER,
        "alternative_folders": ALTERNATIVE_FOLDERS,
        "total_image_count": len(image_metadata),
        "session_image_count": session_image_count
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)