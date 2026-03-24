# ComfyUI Image gen mcpb/connector

This is just a vibecoded garbo thing I wanted to play around with. Basically just an mcp server/installable mcpb designed to wire up comfyUI with claude.

I didn't want to build this up to be a generic "do everything" connector as there are already existing projects for that, this project just has some built-in API workflows with the ability to load one custom one.

It's basically just meant to be pretty dumb and plug and play. Uses either Anima or Flux 2 klein 4B (Q8) - the latter requires Comfy-GGUF, you'll be guided on how to install it.

The releases contain both mcpb and exe.

MCPB is to install it in Claude Desktop. By default images don't really show up properly, you can use my [other project](https://github.com/lugia19/Claude-WebExtension-Launcher) to install Claude QoL in it which fixes that.

EXE just runs it as an HTTP server (optionally with a cloudflare tunnel) so you can wire it up to claude.ai as a connector.

## Quick install guide

1) (Optional-ish) Install Claude Desktop from https://github.com/lugia19/Claude-WebExtension-Launcher
2) Download the latest mcpb file from the releases page
3) Run the launcher, go to Settings > Extensions > Advanced Settings > Install Extension > Install the file you downloaded
4) If a setup window pops up, great, follow it. If it doesn't, restart the client.
5) If after restarting the client two setup windows pop up, close one
6) Follow the setup, pick which models you want to download, etc.
7) If you chose to install Flux Klein, you will need to install Comfy-GGUF in ComfyUI. The model will guide you through the steps as soon as you try to use it and it fails.
8) If anything fails (especially after installing GGUF) restart Claude/ComfyUI/your computer. It's all kind of unreliable.
9) If you don't install a model pack but try to use it, it will download it.
10) If you want to use a custom workflow, or to change which artists the model can choose from, you can go to Settings > Extensions > Configure for comfyui-image-gen. And set it there.