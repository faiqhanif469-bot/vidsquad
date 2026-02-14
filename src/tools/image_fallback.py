"""
Image Fallback System
When video clips are not found for a scene, generate AI image prompts

NOTE: This module generates prompts. To actually generate images with FLUX:
    from src.tools.flux_generator import integrate_with_image_fallback
    
    result = integrate_with_image_fallback(
        scenes=your_scenes,
        extracted_clips=your_clips,
        output_dir="output/generated_images"
    )

See FLUX_SETUP.md for complete setup instructions.
"""

import json
from pathlib import Path
from typing import List, Dict, Optional
from src.core.config import Config
from crewai import LLM


class ImageFallbackGenerator:
    """Generate image prompts for scenes that have no video clips"""
    
    def __init__(self):
        config = Config.load()
        
        # Initialize LLM for prompt generation
        import os
        os.environ['GROQ_API_KEY'] = config.model.groq_api_key
        
        self.llm = LLM(
            model=f"groq/{config.model.model_name}",
            api_key=config.model.groq_api_key
        )
    
    def check_missing_scenes(
        self,
        scenes: List[Dict],
        extracted_clips: List[Dict]
    ) -> List[Dict]:
        """
        Check which scenes have no clips extracted
        
        Args:
            scenes: List of scene dicts from production plan
            extracted_clips: List of extracted clip dicts
        
        Returns:
            List of scenes that have no clips
        """
        print("\n🔍 Checking for scenes without clips...")
        
        # Get scene descriptions that have clips
        scenes_with_clips = set()
        for clip in extracted_clips:
            scenes_with_clips.add(clip.get('scene', ''))
        
        # Find scenes without clips
        missing_scenes = []
        for scene in scenes:
            scene_desc = scene.get('scene_description', '')
            if scene_desc not in scenes_with_clips:
                missing_scenes.append(scene)
        
        print(f"✅ Found {len(missing_scenes)} scenes without clips")
        
        if missing_scenes:
            print("\n📋 Missing scenes:")
            for i, scene in enumerate(missing_scenes, 1):
                print(f"   {i}. Scene {scene.get('scene_number')}: {scene.get('scene_description', '')[:60]}...")
        
        return missing_scenes
    
    def generate_image_prompt(
        self,
        scene: Dict,
        script_context: Optional[str] = None
    ) -> str:
        """
        Generate a detailed image prompt for a scene using AI
        
        Args:
            scene: Scene dict with description, keywords, visual_context
            script_context: Optional script context for better prompts
        
        Returns:
            Detailed image generation prompt
        """
        scene_desc = scene.get('scene_description', '')
        keywords = scene.get('keywords', [])
        visual_context = scene.get('visual_context', '')
        mood = scene.get('mood_tone', '')
        
        # Build context for AI
        context = f"""
Scene Description: {scene_desc}
Visual Context: {visual_context}
Keywords: {', '.join(keywords)}
Mood/Tone: {mood}
"""
        
        if script_context:
            context += f"\nScript Context: {script_context}"
        
        # Generate prompt using AI
        prompt_request = f"""You are an expert at creating detailed image generation prompts for AI image generators like Midjourney, DALL-E, and Stable Diffusion.

Given this scene information:
{context}

Create a SINGLE, detailed image generation prompt that:
1. Captures the LITERAL meaning of the scene (don't over-interpret)
2. Includes specific visual details (composition, lighting, style)
3. Matches the mood and tone
4. Is optimized for photorealistic/historical style
5. Is 1-2 sentences maximum

CRITICAL: Stay LITERAL to the scene description. If it says "rocket launching", describe a rocket launching - not "space exploration" or metaphors.

Output ONLY the image prompt, nothing else."""

        try:
            # Call LLM to generate prompt (without temperature - not supported by CrewAI LLM wrapper)
            response = self.llm.call(
                messages=[{"role": "user", "content": prompt_request}]
            )
            
            # Extract the prompt from response
            if hasattr(response, 'content'):
                image_prompt = response.content.strip()
            else:
                image_prompt = str(response).strip()
            
            print(f"   ✅ AI-generated prompt: {image_prompt[:80]}...")
            return image_prompt
        
        except Exception as e:
            print(f"   ⚠️ Error generating prompt with AI: {e}")
            # Fallback to simple prompt
            return self._generate_simple_prompt(scene)
    
    def _generate_simple_prompt(self, scene: Dict) -> str:
        """Fallback: Generate simple prompt without AI"""
        scene_desc = scene.get('scene_description', '')
        keywords = scene.get('keywords', [])
        mood = scene.get('mood_tone', '')
        
        # Simple template
        prompt = f"{scene_desc}, {', '.join(keywords[:3])}"
        
        if mood:
            prompt += f", {mood} mood"
        
        prompt += ", photorealistic, historical footage style, high quality"
        
        return prompt
    
    def generate_prompts_for_missing_scenes(
        self,
        scenes: List[Dict],
        extracted_clips: List[Dict],
        script_context: Optional[str] = None
    ) -> List[Dict]:
        """
        Complete workflow: Check missing scenes and generate image prompts
        
        Args:
            scenes: All scenes from production plan
            extracted_clips: Clips that were successfully extracted
            script_context: Optional script text for context
        
        Returns:
            List of dicts with scene info and generated image prompts
        """
        print("\n" + "="*80)
        print("🎨 IMAGE FALLBACK GENERATION")
        print("="*80)
        
        # Step 1: Find missing scenes
        missing_scenes = self.check_missing_scenes(scenes, extracted_clips)
        
        if not missing_scenes:
            print("\n✅ All scenes have video clips! No image fallback needed.")
            return []
        
        # Step 2: Generate prompts for missing scenes
        print(f"\n🤖 Generating AI image prompts for {len(missing_scenes)} scenes...")
        
        results = []
        for i, scene in enumerate(missing_scenes, 1):
            scene_num = scene.get('scene_number', i)
            scene_desc = scene.get('scene_description', '')
            
            print(f"\n   Scene {scene_num}: {scene_desc[:60]}...")
            
            # Generate prompt
            image_prompt = self.generate_image_prompt(scene, script_context)
            
            print(f"   ✅ Prompt: {image_prompt[:80]}...")
            
            results.append({
                'scene_number': scene_num,
                'scene_description': scene_desc,
                'keywords': scene.get('keywords', []),
                'image_prompt': image_prompt,
                'visual_context': scene.get('visual_context', ''),
                'mood_tone': scene.get('mood_tone', '')
            })
        
        print(f"\n✅ Generated {len(results)} image prompts")
        
        return results
    
    def save_image_prompts(
        self,
        prompts: List[Dict],
        output_path: str = "output/image_prompts.json"
    ):
        """Save generated prompts to file"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump({
                'total_prompts': len(prompts),
                'prompts': prompts
            }, f, indent=2)
        
        print(f"\n💾 Saved prompts to: {output_file}")
        
        # Also save as text file for easy copy-paste
        txt_file = output_file.with_suffix('.txt')
        with open(txt_file, 'w') as f:
            f.write("IMAGE GENERATION PROMPTS\n")
            f.write("="*80 + "\n\n")
            
            for i, prompt_data in enumerate(prompts, 1):
                f.write(f"Scene {prompt_data['scene_number']}: {prompt_data['scene_description']}\n")
                f.write(f"Keywords: {', '.join(prompt_data['keywords'])}\n")
                f.write(f"\nIMAGE PROMPT:\n{prompt_data['image_prompt']}\n")
                f.write("\n" + "-"*80 + "\n\n")
        
        print(f"💾 Saved text version to: {txt_file}")


def main():
    """Test image fallback generation"""
    
    # Example: Some scenes with clips, some without
    scenes = [
        {
            'scene_number': 1,
            'scene_description': 'rocket launching into space',
            'keywords': ['rocket', 'launch', 'space', 'NASA'],
            'visual_context': 'powerful rocket lifting off from launch pad',
            'mood_tone': 'dramatic, powerful'
        },
        {
            'scene_number': 2,
            'scene_description': 'astronaut floating in space',
            'keywords': ['astronaut', 'spacewalk', 'orbit', 'Earth'],
            'visual_context': 'astronaut in white spacesuit floating with Earth in background',
            'mood_tone': 'peaceful, awe-inspiring'
        },
        {
            'scene_number': 3,
            'scene_description': 'mission control room with engineers',
            'keywords': ['mission control', 'engineers', 'computers', 'NASA'],
            'visual_context': 'busy control room with multiple screens and people working',
            'mood_tone': 'focused, intense'
        }
    ]
    
    # Simulate: Only scene 1 has clips
    extracted_clips = [
        {
            'scene': 'rocket launching into space',
            'path': 'output/clip1.mp4'
        }
    ]
    
    # Generate prompts for missing scenes
    generator = ImageFallbackGenerator()
    prompts = generator.generate_prompts_for_missing_scenes(
        scenes,
        extracted_clips,
        script_context="Documentary about the Apollo moon landing mission"
    )
    
    # Save prompts
    generator.save_image_prompts(prompts)
    
    print("\n" + "="*80)
    print("✅ COMPLETE!")
    print("="*80)
    print(f"Generated {len(prompts)} image prompts for missing scenes")
    print("You can now use these prompts with:")
    print("  - Midjourney")
    print("  - DALL-E")
    print("  - Stable Diffusion")
    print("  - Leonardo.ai")
    print("  - Any other AI image generator")


if __name__ == "__main__":
    main()

