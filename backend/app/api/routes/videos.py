"""
Video Generation API Routes
Main pipeline endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.dependencies import get_current_user, check_rate_limit
from app.workers.tasks.pipeline_task import run_full_pipeline
from app.services.queue_service import QueueService
from firebase_admin import firestore
import uuid

from app.dependencies import db

router = APIRouter()
queue_service = QueueService()


class VideoGenerationRequest(BaseModel):
    """Video generation request"""
    script: str
    duration: int = 60
    title: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Job status response"""
    job_id: str
    status: str
    progress: int
    current_step: Optional[str] = None
    eta_seconds: Optional[int] = None
    error: Optional[str] = None
    result: Optional[dict] = None


@router.post("/generate")
async def generate_video(request: VideoGenerationRequest):
    """
    Start video generation pipeline
    
    Steps:
    1. AI script analysis
    2. Video search
    3. Download & extract clips
    4. Generate AI images for missing scenes
    5. Export to Premiere Pro & CapCut
    
    Returns job_id for status tracking
    """
    try:
        # Create job ID
        job_id = str(uuid.uuid4())
        user_id = "anonymous"  # No auth for now
        
        # Create project in Firestore (if available)
        if db is not None:
            project_ref = db.collection('projects').document(job_id)
            project_ref.set({
                'job_id': job_id,
                'user_id': user_id,
                'title': request.title or 'Untitled Video',
                'script': request.script,
                'duration': request.duration,
                'status': 'queued',
                'progress': 0,
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': firestore.SERVER_TIMESTAMP
            })
        
        # Queue job to Celery
        task = run_full_pipeline.delay(
            job_id=job_id,
            user_id=user_id,
            script=request.script,
            duration=request.duration
        )
        
        # Store task ID (if Firestore available)
        if db is not None:
            project_ref.update({
                'task_id': task.id
            })
        
        return {
            'success': True,
            'job_id': job_id,
            'task_id': task.id,
            'status': 'queued',
            'message': 'Video generation started. Check status with /api/videos/status/{job_id}'
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get job status and progress
    
    Returns:
    - status: queued, processing, completed, failed
    - progress: 0-100
    - current_step: Current processing step
    - eta_seconds: Estimated time remaining
    - result: Download links (when completed)
    """
    try:
        # Try Redis first (fastest, always available)
        import redis
        from app.config import settings
        
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        progress_json = redis_client.get(f"job_progress:{job_id}")
        
        if progress_json:
            import json
            progress_data = json.loads(progress_json)
            
            # Check if completed, get result from Redis
            if progress_data.get('status') == 'completed':
                result_json = redis_client.get(f"job_result:{job_id}")
                if result_json:
                    progress_data['result'] = json.loads(result_json)
            
            return progress_data
        
        # Fallback to Firestore (if available)
        if db is not None:
            project_ref = db.collection('projects').document(job_id)
            project = project_ref.get()
            
            if project.exists:
                project_data = project.to_dict()
                
                return {
                    'job_id': job_id,
                    'status': project_data.get('status', 'unknown'),
                    'progress': project_data.get('progress', 0),
                    'current_step': project_data.get('current_step'),
                    'eta_seconds': project_data.get('eta_seconds'),
                    'error': project_data.get('error'),
                    'result': project_data.get('result')
                }
        
        # Last resort: Check Celery task status
        from app.workers.celery_app import celery_app
        
        inspect = celery_app.control.inspect()
        
        # Check active tasks
        active = inspect.active()
        if active:
            for worker, tasks in active.items():
                for task in tasks:
                    if job_id in str(task.get('args', [])):
                        return {
                            'job_id': job_id,
                            'status': 'processing',
                            'progress': 50,
                            'current_step': 'Processing video...',
                            'eta_seconds': 60
                        }
        
        # Check reserved tasks (queued)
        reserved = inspect.reserved()
        if reserved:
            for worker, tasks in reserved.items():
                for task in tasks:
                    if job_id in str(task.get('args', [])):
                        return {
                            'job_id': job_id,
                            'status': 'queued',
                            'progress': 0,
                            'current_step': 'Waiting in queue...',
                            'eta_seconds': 120
                        }
        
        # Not found - might be completed or very old
        raise HTTPException(status_code=404, detail="Job not found")
    
    except HTTPException:
        raise
    except Exception as e:
        # Return generic processing status on error
        return {
            'job_id': job_id,
            'status': 'processing',
            'progress': 50,
            'current_step': 'Processing...',
            'eta_seconds': 60
        }


@router.get("/download/{job_id}")
async def get_download_links(job_id: str):
    """
    Get download links for completed job
    
    Returns:
    - premiere_url: Premiere Pro project download link
    - capcut_url: CapCut project download link
    - expires_at: Link expiration time
    """
    try:
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")
        
        # Get project from Firestore
        project_ref = db.collection('projects').document(job_id)
        project = project_ref.get()
        
        if not project.exists:
            raise HTTPException(status_code=404, detail="Job not found")
        
        project_data = project.to_dict()
        
        # Check if completed
        if project_data.get('status') != 'completed':
            raise HTTPException(
                status_code=400,
                detail=f"Job not completed yet. Status: {project_data.get('status')}"
            )
        
        # Get download links from result
        result = project_data.get('result', {})
        
        return {
            'job_id': job_id,
            'premiere_url': result.get('premiere_url'),
            'capcut_url': result.get('capcut_url'),
            'expires_at': result.get('expires_at'),
            'clips_count': result.get('clips_count', 0),
            'images_count': result.get('images_count', 0)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{job_id}")
async def delete_job(job_id: str):
    """
    Delete job and associated files
    """
    try:
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")
        
        # Get project from Firestore
        project_ref = db.collection('projects').document(job_id)
        project = project_ref.get()
        
        if not project.exists:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Delete from Firestore
        project_ref.delete()
        
        # TODO: Delete files from DigitalOcean Spaces
        
        return {
            'success': True,
            'message': 'Job deleted successfully'
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue/status")
async def get_queue_status():
    """
    Get current queue status
    
    Returns:
    - pending_jobs: Number of jobs in queue
    - processing_jobs: Number of jobs currently processing
    - estimated_wait_time: Estimated wait time in seconds
    """
    try:
        queue_stats = queue_service.get_queue_stats()
        
        return {
            'pending_jobs': queue_stats['pending'],
            'processing_jobs': queue_stats['processing'],
            'completed_jobs': queue_stats['completed'],
            'failed_jobs': queue_stats['failed'],
            'estimated_wait_time_seconds': queue_stats['estimated_wait_time']
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
