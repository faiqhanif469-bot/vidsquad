"""
Main Pipeline Task
Orchestrates the complete video generation pipeline
"""

from app.workers.celery_app import celery_app
from firebase_admin import firestore
from datetime import datetime, timedelta
import sys
import os
import redis
import json

# Add parent directory to path to import src modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.core.config import Config
from src.agents.crew import ProductionCrew
from src.tools.fast_search import FastVideoSearch
from src.tools.downloader import VideoDownloader
from src.tools.broll_extractor import BRollExtractor
from src.tools.flux_generator import integrate_with_image_fallback
from src.tools.premiere_exporter import PremiereExporter
from src.tools.capcut_exporter import CapCutExporter
from app.services.storage_service import StorageService
from app.dependencies import db
from app.config import settings

storage_service = StorageService()

# Redis client for progress tracking
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


def update_job_progress(job_id: str, status: str, progress: int, current_step: str = None, eta_seconds: int = None):
    """Update job progress in Redis and Firestore"""
    
    # Update Redis (always available)
    progress_data = {
        'job_id': job_id,
        'status': status,
        'progress': progress,
        'current_step': current_step or status,
        'eta_seconds': eta_seconds or 0,
        'updated_at': datetime.utcnow().isoformat()
    }
    
    # Store in Redis with 1 hour expiration
    redis_client.setex(
        f"job_progress:{job_id}",
        3600,  # 1 hour TTL
        json.dumps(progress_data)
    )
    
    # Also update Firestore if available
    if db is not None:
        project_ref = db.collection('projects').document(job_id)
        update_data = {
            'status': status,
            'progress': progress,
            'updated_at': firestore.SERVER_TIMESTAMP
        }
        
        if current_step:
            update_data['current_step'] = current_step
        if eta_seconds:
            update_data['eta_seconds'] = eta_seconds
        
        try:
            project_ref.update(update_data)
        except:
            pass  # Ignore Firestore errors
    
    # Print for logs
    print(f"Progress: {progress}% - {current_step or status}")


@celery_app.task(bind=True, name='run_full_pipeline')
def run_full_pipeline(self, job_id: str, user_id: str, script: str, duration: int):
    """
    Run complete video generation pipeline
    
    Steps:
    1. AI Script Analysis (20%)
    2. Video Search (40%)
    3. Download & Extract Clips (60%)
    4. Generate AI Images (80%)
    5. Export Projects (100%)
    """
    try:
        output_dir = f"temp/{user_id}/{job_id}"
        os.makedirs(output_dir, exist_ok=True)
        
        # STEP 1: AI Script Analysis
        update_job_progress(job_id, 'processing', 10, 'Analyzing script with AI...', 240)
        
        config = Config.load()
        crew = ProductionCrew(config)
        result = crew.analyze_script(script, duration)
        
        # Parse result with robust JSON extraction
        import re
        import json
        
        # Try to extract JSON from the result
        result_str = str(result)
        
        # Try multiple JSON extraction methods
        plan = None
        
        # Method 1: Find JSON block with regex
        json_match = re.search(r'\{.*\}', result_str, re.DOTALL)
        if json_match:
            try:
                plan = json.loads(json_match.group())
            except json.JSONDecodeError as e:
                print(f"JSON parse error (method 1): {e}")
                
                # Method 2: Try to fix common JSON issues
                json_str = json_match.group()
                # Remove trailing commas
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                # Fix single quotes to double quotes
                json_str = json_str.replace("'", '"')
                
                try:
                    plan = json.loads(json_str)
                except json.JSONDecodeError:
                    print("JSON parse error (method 2): Still invalid")
        
        # If still no plan, create a simple fallback based on the script
        if not plan:
            print("Creating fallback plan from script")
            
            # Split script into sentences for scenes
            sentences = [s.strip() for s in script.split('.') if s.strip()]
            num_scenes = min(len(sentences), 12)  # Max 12 scenes
            
            if num_scenes < 6:
                num_scenes = 6  # Minimum 6 scenes
            
            plan = {
                'title': 'AI Generated Video',
                'scenes': []
            }
            
            for i in range(num_scenes):
                # Use script sentences or generic descriptions
                if i < len(sentences):
                    scene_desc = sentences[i]
                else:
                    scene_desc = f"Scene {i+1} from the video"
                
                # Extract keywords from description
                words = scene_desc.lower().split()
                keywords = [w for w in words if len(w) > 4][:5]  # Top 5 long words
                
                plan['scenes'].append({
                    'scene_number': i + 1,
                    'scene_description': scene_desc,
                    'duration': 5,
                    'visual_context': scene_desc,
                    'mood_tone': 'informative, engaging',
                    'keywords': keywords,
                    'search_queries': []  # No video search for fallback
                })
            
            print(f"Created fallback plan with {num_scenes} scenes")
        
        update_job_progress(job_id, 'processing', 20, 'Script analyzed', 180)
        
        # STEP 2: Video Search
        update_job_progress(job_id, 'processing', 30, 'Searching for videos...', 150)
        
        # Use channel-based search (bypasses YouTube bot detection)
        from src.tools.channel_video_finder import ChannelVideoFinder
        
        channel_finder = ChannelVideoFinder()
        
        for scene in plan.get('scenes', []):
            try:
                # Find videos from curated channels
                videos = channel_finder.find_videos_for_scene(scene)
                
                # Add results to scene
                if 'search_queries' not in scene:
                    scene['search_queries'] = []
                
                # Convert to expected format
                scene['search_queries'].append({
                    'query': scene.get('scene_description', '')[:50],
                    'results_found': len(videos),
                    'sample_videos': [
                        {
                            'title': v['title'],
                            'url': v['url'],
                            'duration': v['duration'],
                            'relevance_score': 0.8  # Channel videos are pre-vetted
                        }
                        for v in videos[:3]
                    ]
                })
            except Exception as e:
                print(f"Search error for scene {scene.get('scene_number')}: {e}")
                continue
        
        update_job_progress(job_id, 'processing', 40, 'Videos found', 120)
        
        # STEP 3: Extract Clips (PARALLEL extraction without full download)
        update_job_progress(job_id, 'processing', 50, 'Extracting clips in parallel...', 90)
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        extractor = BRollExtractor(output_dir=f"{output_dir}/clips")
        extracted_clips = []
        
        def extract_clip_for_scene(scene):
            """Extract clip for a single scene (runs in parallel)"""
            scene_num = scene.get('scene_number')
            scene_duration = scene.get('duration', 5)
            scene_desc = scene.get('scene_description', '')
            
            # Get best video
            best_video = None
            for query_obj in scene.get('search_queries', []):
                videos = query_obj.get('sample_videos', [])
                if videos:
                    best_video = videos[0]
                    break
            
            if not best_video:
                return None
            
            try:
                # Use existing broll_extractor to extract clip directly
                clips = extractor._extract_random_clips(
                    video={
                        'url': best_video['url'],
                        'id': best_video['url'].split('=')[-1],  # Extract video ID from URL
                        'title': best_video.get('title', 'Unknown')
                    },
                    scene_description=scene_desc,
                    duration=scene_duration,
                    num_clips=1
                )
                
                if clips:
                    return {
                        'scene': scene_desc,
                        'scene_number': scene_num,
                        'path': clips[0]['path'],
                        'source_url': best_video['url']
                    }
            except Exception as e:
                print(f"Error extracting clip for scene {scene_num}: {e}")
                return None
        
        # Extract clips in parallel (max 6 concurrent downloads)
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(extract_clip_for_scene, scene): scene for scene in plan.get('scenes', [])}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    extracted_clips.append(result)
                    print(f"✅ Extracted clip for scene {result['scene_number']}")
        
        print(f"\n✅ Total clips extracted: {len(extracted_clips)}")
        
        update_job_progress(job_id, 'processing', 60, f'{len(extracted_clips)} clips extracted', 60)
        
        # STEP 4: Generate AI Images
        update_job_progress(job_id, 'processing', 70, 'Generating AI images...', 40)
        
        result = integrate_with_image_fallback(
            scenes=plan.get('scenes', []),
            extracted_clips=extracted_clips,
            output_dir=f"{output_dir}/images",
            provider="cloudflare"
        )
        
        update_job_progress(job_id, 'processing', 80, f'{result.get("generated_images", 0)} images generated', 20)
        
        # STEP 5: Export Projects
        update_job_progress(job_id, 'processing', 90, 'Creating project files...', 10)
        
        project_name = plan.get('title', 'AI_Video').replace(' ', '_')
        
        # Export to Premiere Pro
        premiere_exporter = PremiereExporter()
        premiere_path = premiere_exporter.create_premiere_project(
            clips=extracted_clips,
            images=result.get('images', []),
            output_dir=output_dir,
            project_name=project_name
        )
        
        # Export to CapCut
        capcut_exporter = CapCutExporter()
        capcut_path = capcut_exporter.create_capcut_project(
            clips=extracted_clips,
            images=result.get('images', []),
            output_dir=output_dir,
            project_name=project_name
        )
        
        # Upload to DigitalOcean Spaces
        premiere_url = storage_service.upload_folder(premiere_path, f"{user_id}/{job_id}/premiere")
        capcut_url = storage_service.upload_folder(capcut_path, f"{user_id}/{job_id}/capcut")
        
        # Schedule cleanup (20 minutes)
        expires_at = datetime.utcnow() + timedelta(minutes=20)
        
        # Prepare result data
        result_data = {
            'premiere_url': premiere_url,
            'capcut_url': capcut_url,
            'clips_count': len(extracted_clips),
            'images_count': result.get('generated_images', 0),
            'expires_at': expires_at.isoformat()
        }
        
        # Store result in Redis (1 hour TTL)
        redis_client.setex(
            f"job_result:{job_id}",
            3600,
            json.dumps(result_data)
        )
        
        # Update progress to completed
        update_job_progress(job_id, 'completed', 100, 'Completed!', 0)
        
        # Update job as completed in Firestore (if available)
        if db is not None:
            try:
                project_ref = db.collection('projects').document(job_id)
                project_ref.update({
                    'status': 'completed',
                    'progress': 100,
                    'current_step': 'Completed',
                    'result': result_data,
                    'completed_at': firestore.SERVER_TIMESTAMP,
                    'updated_at': firestore.SERVER_TIMESTAMP
                })
                
                # Increment user's video count
                user_ref = db.collection('users').document(user_id)
                user_ref.update({
                    'videos_created_this_month': firestore.Increment(1)
                })
            except:
                pass  # Ignore Firestore errors
        
        # TODO: Schedule cleanup after 20 minutes (implement later)
        
        return {
            'success': True,
            'job_id': job_id,
            'premiere_url': premiere_url,
            'capcut_url': capcut_url
        }
    
    except Exception as e:
        # Update job as failed in Redis
        error_msg = str(e)
        redis_client.setex(
            f"job_progress:{job_id}",
            3600,
            json.dumps({
                'job_id': job_id,
                'status': 'failed',
                'progress': 0,
                'current_step': 'Failed',
                'error': error_msg,
                'updated_at': datetime.utcnow().isoformat()
            })
        )
        
        # Update job as failed in Firestore (if available)
        if db is not None:
            try:
                project_ref = db.collection('projects').document(job_id)
                project_ref.update({
                    'status': 'failed',
                    'error': error_msg,
                    'updated_at': firestore.SERVER_TIMESTAMP
                })
            except:
                pass  # Ignore Firestore errors
        
        raise
