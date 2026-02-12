"""
FastAPI Dependencies
Authentication, rate limiting, etc.
"""

from fastapi import Header, HTTPException, Depends
from typing import Optional
import firebase_admin
from firebase_admin import auth, credentials, firestore
from app.config import settings
import redis
from functools import wraps
import time

# Initialize Firebase
firebase_initialized = False
db = None

if settings.FIREBASE_CREDENTIALS_PATH:
    try:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        firebase_initialized = True
    except Exception as e:
        print(f"Warning: Firebase initialization failed: {e}")
        print("Running without Firebase authentication")

# Initialize Redis
redis_client = redis.from_url(settings.REDIS_URL)


async def verify_firebase_token(authorization: Optional[str] = Header(None)) -> dict:
    """
    Verify Firebase authentication token
    
    Args:
        authorization: Bearer token from header
    
    Returns:
        User data dict
    
    Raises:
        HTTPException: If token is invalid
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="No authorization header")
    
    try:
        # Extract token from "Bearer <token>"
        token = authorization.replace("Bearer ", "")
        
        # Verify token with Firebase
        decoded_token = auth.verify_id_token(token)
        
        # Get user data
        user_id = decoded_token['uid']
        email = decoded_token.get('email')
        
        return {
            'user_id': user_id,
            'email': email,
            'token': decoded_token
        }
    
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


async def get_current_user(user_data: dict = Depends(verify_firebase_token)) -> dict:
    """
    Get current user from Firestore
    
    Args:
        user_data: Verified user data from token
    
    Returns:
        User document from Firestore
    """
    user_id = user_data['user_id']
    
    # Get user from Firestore
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        # Create new user
        user_data_firestore = {
            'user_id': user_id,
            'email': user_data['email'],
            'subscription_tier': 'free',
            'videos_created_this_month': 0,
            'created_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP
        }
        user_ref.set(user_data_firestore)
        return user_data_firestore
    
    return user_doc.to_dict()


async def check_rate_limit(user: dict = Depends(get_current_user)):
    """
    Check if user has exceeded rate limit
    
    Args:
        user: Current user data
    
    Raises:
        HTTPException: If rate limit exceeded
    """
    tier = user.get('subscription_tier', 'free')
    videos_created = user.get('videos_created_this_month', 0)
    
    # Get rate limit for tier
    limits = {
        'free': settings.RATE_LIMIT_FREE,
        'pro': settings.RATE_LIMIT_PRO,
        'enterprise': settings.RATE_LIMIT_ENTERPRISE
    }
    
    limit = limits.get(tier, settings.RATE_LIMIT_FREE)
    
    if videos_created >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Upgrade to create more videos. ({videos_created}/{limit})"
        )
    
    return user


def cache_response(ttl: int = 3600):
    """
    Cache decorator for API responses
    
    Args:
        ttl: Time to live in seconds
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = f"cache:{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # Check cache
            cached = redis_client.get(cache_key)
            if cached:
                return eval(cached)
            
            # Call function
            result = await func(*args, **kwargs)
            
            # Store in cache
            redis_client.setex(cache_key, ttl, str(result))
            
            return result
        return wrapper
    return decorator


def rate_limit_api(max_requests: int = 100, window: int = 60):
    """
    Rate limit decorator for API endpoints
    
    Args:
        max_requests: Max requests per window
        window: Time window in seconds
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get user from kwargs
            user = kwargs.get('user')
            if not user:
                return await func(*args, **kwargs)
            
            user_id = user.get('user_id')
            
            # Rate limit key
            key = f"rate_limit:{user_id}:{int(time.time() / window)}"
            
            # Increment counter
            count = redis_client.incr(key)
            
            # Set expiry on first request
            if count == 1:
                redis_client.expire(key, window)
            
            # Check limit
            if count > max_requests:
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many requests. Try again in {window} seconds."
                )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator
