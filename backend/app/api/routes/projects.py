"""
Projects Routes
"""

from fastapi import APIRouter, Depends, HTTPException
from app.dependencies import get_current_user
from firebase_admin import firestore

from app.dependencies import db

router = APIRouter()


@router.get("/")
async def list_projects(user: dict = Depends(get_current_user)):
    """
    List all user's projects
    """
    try:
        projects_ref = db.collection('projects').where('user_id', '==', user['user_id'])
        projects = projects_ref.order_by('created_at', direction=firestore.Query.DESCENDING).limit(50).stream()
        
        result = []
        for project in projects:
            data = project.to_dict()
            result.append({
                'job_id': data['job_id'],
                'title': data['title'],
                'status': data['status'],
                'progress': data.get('progress', 0),
                'created_at': data.get('created_at'),
                'completed_at': data.get('completed_at')
            })
        
        return {
            'success': True,
            'projects': result,
            'total': len(result)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}")
async def get_project(job_id: str, user: dict = Depends(get_current_user)):
    """
    Get project details
    """
    try:
        project_ref = db.collection('projects').document(job_id)
        project = project_ref.get()
        
        if not project.exists:
            raise HTTPException(status_code=404, detail="Project not found")
        
        data = project.to_dict()
        
        if data['user_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Access denied")
        
        return {
            'success': True,
            'project': data
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
