"""
WebSocket Routes for Real-time Progress Updates
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from firebase_admin import firestore
import asyncio
import json

from app.dependencies import db

router = APIRouter()


@router.websocket("/progress/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str):
    """
    WebSocket endpoint for real-time job progress updates
    
    Frontend connects to: ws://api.yourdomain.com/ws/progress/{job_id}
    Receives updates every 2 seconds
    """
    await websocket.accept()
    
    try:
        while True:
            # Get project status from Firestore
            project_ref = db.collection('projects').document(job_id)
            project = project_ref.get()
            
            if not project.exists:
                await websocket.send_json({
                    'error': 'Job not found'
                })
                break
            
            data = project.to_dict()
            
            # Send progress update
            await websocket.send_json({
                'job_id': job_id,
                'status': data.get('status'),
                'progress': data.get('progress', 0),
                'current_step': data.get('current_step'),
                'eta_seconds': data.get('eta_seconds'),
                'error': data.get('error'),
                'result': data.get('result')
            })
            
            # If completed or failed, close connection
            if data.get('status') in ['completed', 'failed']:
                break
            
            # Wait 2 seconds before next update
            await asyncio.sleep(2)
    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({
            'error': str(e)
        })
    finally:
        await websocket.close()
