@echo off
set MODELS=%USERPROFILE%\Documents\ComfyUI\models

del /f "%MODELS%\diffusion_models\anima-preview2.safetensors" 2>nul
del /f "%MODELS%\text_encoders\qwen_3_06b_base.safetensors" 2>nul
del /f "%MODELS%\vae\qwen_image_vae.safetensors" 2>nul

del /f "%MODELS%\diffusion_models\flux-2-klein-4b-Q8_0.gguf" 2>nul
del /f "%MODELS%\text_encoders\qwen_3_4b_fp4_flux2.safetensors" 2>nul
del /f "%MODELS%\vae\flux2-vae.safetensors" 2>nul

echo Done.
pause
