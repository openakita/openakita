


# Terminal 1 — backend
cd /home/devops/agents/openakita
source .venv/bin/activate
openakita serve

# Terminal 2 — frontend  
cd /home/devops/agents/openakita/apps/setup-center
VITE_BUILD_TARGET=web npm run dev



