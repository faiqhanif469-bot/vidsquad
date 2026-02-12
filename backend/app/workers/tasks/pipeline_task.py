"""
Main Pipeline Task
Orchestrates the complete video generation pipeline
"""

from app.workers.celery_app import celery_app
from firebase_admin import firestore
from datetime import datetime, timedelta
import sys
import os

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

storage_service = StorageService()


def update_job_progress(job_id: str, status: str, progress: int, current_step: str = None, eta_seconds: int = None):
    """Update job progress in Firestore"""
    if db is None:
        print(f"Progress: {progress}% - {current_step or status}")
        return
    
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
    
    project_ref.update(update_data)


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
        
        # Parse result
        import re
        import json
        json_match = re.search(r'\{.*\}', str(result), re.DOTALL)
        if not json_match:
            raise Exception("Failed to parse AI analysis")
        
        plan = json.loads(json_match.group())
        
        update_job_progress(job_id, 'processing', 20, 'Script analyzed', 180)
        
        # STEP 2: Video Search
        update_job_progress(job_id, 'processing', 30, 'Searching for videos...', 150)
        
        searcher = FastVideoSearch()
        
        for scene in plan.get('scenes', []):
            for query_obj in scene.get('search_queries', []):
                query = query_obj.get('query', '')
                if not query:
                    continue
                
                results = searcher.intelligent_search(
                    query=query,
                    context=scene.get('visual_context', ''),
                    platforms=['youtube']
                )
                
                query_obj['results_found'] = len(results)
                query_obj['sample_videos'] = [
                    {
                        'title': r['title'],
                        'url': r['url'],
                        'duration': r['duration'],
                        'relevance_score': round(r['relevance_score'], 2)
                    }
                    for r in results[:3]
                ]
        
        update_job_progress(job_id, 'processing', 40, 'Videos found', 120)
        
        # STEP 3: Download & Extract Clips
        update_job_progress(job_id, 'processing', 50, 'Downloading videos...', 90)
        
        downloader = VideoDownloader()
        extractor = BRollExtractor()
        extracted_clips = []
        
        for scene in plan.get('scenes', []):
            scene_num = scene.get('scene_number')
            
            # Get best video
            best_video = None
            for query_obj in scene.get('search_queries', []):
                videos = query_obj.get('sample_videos', [])
                if videos:
                    best_video = videos[0]
                    break
            
            if not best_video:
                continue
            
            try:
                # Download
                video_path = downloader.download(
                    url=best_video['url'],
                    output_dir=f"{output_dir}/downloads"
                )
                
                if not video_path:
                    continue
                
                # Extract clip
                clip_path = extractor.extract_best_clip(
                    video_path=video_path,
                    duration=scene.get('duration', 5),
                    output_dir=f"{output_dir}/clips"
                )
                
                if clip_path:
                    extracted_clips.append({
                        'scene': scene.get('scene_description', ''),
                        'scene_number': scene_num,
                        'path': clip_path,
                        'source_url': best_video['url']
                    })
            except:
                continue
        
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
        
        # Update job as completed
        if db is not None:
            project_ref = db.collection('projects').document(job_id)
            project_ref.update({
                'status': 'completed',
                'progress': 100,
                'current_step': 'Completed',
                'result': {
                    'premiere_url': premiere_url,
                    'capcut_url': capcut_url,
                    'clips_count': len(extracted_clips),
                    'images_count': result.get('generated_images', 0),
                    'expires_at': expires_at.isoformat()
                },
                'completed_at': firestore.SERVER_TIMESTAMP,
                'updated_at': firestore.SERVER_TIMESTAMP
            })
            
            # Increment user's video count
            user_ref = db.collection('users').document(user_id)
            user_ref.update({
                'videos_created_this_month': firestore.Increment(1)
            })
        
        # Schedule cleanup
        from app.workers.tasks.cleanup_task import cleanup_job
        cleanup_job.apply_async(args=[job_id, user_id], countdown=1200)  # 20 minutes
        
        return {
            'success': True,
            'job_id': job_id,
            'premiere_url': premiere_url,
            'capcut_url': capcut_url
        }
    
    except Exception as e:
        # Update job as failed
        if db is not None:
            project_ref = db.collection('projects').document(job_id)
            project_ref.update({
                'status': 'failed',
                'error': str(e),
                'updated_at': firestore.SERVER_TIMESTAMP
            })
        
        raise
