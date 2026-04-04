# AgentScript: Local Deployment & Free Cloud Options

## 🚀 Option 1: Run Locally (Direct Python - No Docker Required)

### Prerequisites
- Python 3.10+ (you have 3.13.7 ✅)
- Git (for version control)
- pip (Python package manager)

### Step-by-Step Local Setup

```powershell
# 1. Navigate to AgentScript directory
cd "C:\Users\email\OneDrive\Documents\Playground\AgentScript"

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install fastapi uvicorn sqlite3 opentelemetry-api opentelemetry-sdk

# 4. Run the legal research demo
python -m agentscript.cli.main run examples/legal_research.as --demo legal --mode replay

# 5. Run the dashboard
python -m agentscript.observability.server --host 0.0.0.0 --port 8000

# 6. Open in browser
Start-Process "http://127.0.0.1:8000"
```

**Dashboard will be available at:** http://127.0.0.1:8000

---

## 🌍 Option 2: Free Cloud Deployment Platforms

### **1. Railway.app** (RECOMMENDED - $5 free credit)
**Free Tier:** 24/7 uptime, $5/month free credit

**Deployment steps:**
```bash
# 1. Sign up: https://railway.app
# 2. Create new project
# 3. Connect GitHub repo: https://github.com/geek-code-psj/AgentScript_fin.git
# 4. Railway auto-detects Python from pyproject.toml
# 5. Set environment variables in dashboard
# 6. Deploy with: git push origin main
```

**Cost estimate:** FREE + $5 credit/month (sufficient for small agents)

---

### **2. Heroku (Legacy Free Tier Closed - PAID)**
As of Nov 2022, Heroku free tier is gone. Minimum cost: $7/month for hobby dyno.

---

### **3. Replit** (RECOMMENDED - Free Tier Available)
**Free Tier:** Unlimited public projects, 0.5GB RAM limited

**Deployment:**
```bash
# 1. Go to https://replit.com
# 2. Click "Create"
# 3. Import from GitHub: https://github.com/geek-code-psj/AgentScript_fin.git
# 4. Replit auto-installs from pyproject.toml
# 5. Run: python -m agentscript.cli.main run examples/legal_research.as
```

**Pros:** 
- No credit card needed
- Instant deployment
- Built-in editor
- Always-on option available

**Cons:**
- 0.5GB RAM (limited for large agent runs)
- Public by default

---

### **4. Google Cloud Run** (RECOMMENDED - Free Tier)
**Free Tier:** 2M requests/month, 360K GB-seconds/month = ~FREE for testing

**Deployment:**
```bash
# 1. Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install
# 2. Authenticate: gcloud auth login
# 3. Create Dockerfile (already in repo)
# 4. Build and deploy:

gcloud run deploy agentscript \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated

# 5. Cloud Run provides a public URL
```

**Cost:** FREE for 2M requests + 360K GB-seconds/month
**LLM API calls not included** (use own API keys)

---

### **5. AWS Lambda + API Gateway** (Free Tier)
**Free Tier:** 1M API Gateway requests, 1M Lambda requests/month = FREE

**Deployment:**
```bash
# Requires more setup - not recommended for full agents
# Better for serverless microservices than long-running agents
# See AWS documentation for details
```

**Cost:** FREE for first year with free tier credits

---

### **6. PythonAnywhere** (RECOMMENDED - Free Tier)
**Free Tier:** Python hosting with limited CPU/disk

**Deployment:**
```bash
# 1. Sign up: https://www.pythonanywhere.com
# 2. Upload via Git to Web app
# 3. Configure WSGI file for FastAPI
# 4. No credit card required
```

**Pros:** 
- Python-specific hosting
- Web framework support
- Always-on free tier

**Cons:**
- Limited to 100MB disk
- CPU time limits
- File size restrictions

---

### **7. Fly.io** (RECOMMENDED - $3 free/month)
**Free Tier:** Shared CPU instances, $3/month free allowance

**Deployment:**
```bash
# 1. Sign up: https://fly.io
# 2. Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
# 3. Init and deploy:

flyctl auth login
flyctl launch --builder=docker
flyctl deploy

# 4. Fly.io provides a .fly.dev domain
```

**Cost:** FREE with $3/month allowance (sufficient for hobby projects)

---

### **8. OpenShift (Red Hat)** - Free Developer Sandbox
**Free Tier:** Kubernetes cluster, 24/7 uptime

**Deployment:**
```bash
# 1. Sign up: https://developers.redhat.com/products/openshift/getting-started
# 2. Get sandbox cluster
# 3. Deploy Helm chart:

helm install agentscript ./deployment/k8s/agentscript-helm \
  --namespace default \
  --kubeconfig=<sandbox-config>
```

**Cost:** FREE (Red Hat's free Kubernetes sandbox)

---

### **9. Oracle Cloud** (RECOMMENDED - Always Free)
**Free Tier:** 24GB RAM, 4 CPUs, 200GB storage = Genuinely FREE

**Deployment:**
```bash
# 1. Sign up: https://www.oracle.com/cloud/free/
# 2. Create 2 Compute instances (1 core, 1GB RAM each = free)
# 3. Install Python and Docker:

sudo yum install python3 docker -y
docker build -t agentscript .
docker run -p 8000:8000 agentscript

# Or use their Container Registry (free)
```

**Cost:** COMPLETELY FREE (no time limit, no credit card)

---

### **10. DigitalOcean App Platform** ($5/month)
**Free Tier:** $5/month Pro credit or App Starter Plan

**Deployment:**
```bash
# 1. Sign up: https://www.digitalocean.com/
# 2. Connect GitHub repo
# 3. DigitalOcean detects Dockerfile and deploys
# 4. Automatic HTTPS, auto-scaling
```

**Cost:** $5-12/month minimum (cheapest full-managed option)

---

## 📊 Comparison Table

| Platform | Cost | Uptime | CPU | RAM | Recommended |
|----------|------|--------|-----|-----|-------------|
| **Railway** | $5/mo free | 24/7 | 2x | 2GB | ✅ BEST |
| **Fly.io** | $3/mo | 24/7 | 2x | 256MB | ✅ GOOD |
| **Replit** | FREE | Varies | 1x | 512MB | ✅ EASY |
| **Google Cloud Run** | FREE* | Serverless | 2x | 512MB | ✅ SCALABLE |
| **Oracle Cloud** | FREE | 24/7 | 1x | 1GB | ✅ BEST VALUE |
| **PythonAnywhere** | FREE | 24/7 | Limited | 100MB | ⚠️ BASIC |
| **Heroku** | $7/mo | 24/7 | 1x | 512MB | ❌ PAID |
| **AWS Lambda** | FREE* | Serverless | - | 1-10GB | ⚠️ COMPLEX |
| **Azure App Service** | FREE | 24/7 | 1x | 1GB | ⚠️ COMPLEX |
| **DigitalOcean** | $5-12/mo | 24/7 | 1x | 512MB | ⚠️ PAID |

*Free tier with usage limits

---

## 🎯 RECOMMENDED DEPLOYMENT PATH

### For Testing/Learning (Completely FREE)
```
1. Replit or Google Cloud Run (instant, zero setup)
2. Upload: GitHub repo → auto-deploys
3. Test agent flows and observability
```

### For Production (Still FREE or <$5/month)
```
1. Oracle Cloud (Always Free tier)
   - Deploy via Docker
   - 24GB RAM potential
   - No expiration
   
OR

2. Railway.app ($5/month)
   - GitHub auto-deploy
   - Professional infrastructure
   - Easy scaling
```

### For Kubernetes (Production Grade)
```
1. OpenShift Developer Sandbox (free Kubernetes)
2. Deploy Helm chart:
   helm install agentscript ./deployment/k8s/agentscript-helm
```

---

## 🔧 Quick Start Commands by Platform

### **Railway.app (Recommended)**
```bash
# 1. Create railway.toml in repo root:
[build]
builder = "dockerfile"

[deploy]
port = 8000
restartPolicyType = "always"
healthcheckPath = "/health"

# 2. Push to GitHub
git add railway.toml
git commit -m "Add railway config"
git push origin main

# 3. Open Railway dashboard and connect GitHub repo
# Automatic deployment starts!
```

### **Google Cloud Run (Recommended)**
```bash
# Requires Google Cloud SDK installed
gcloud run deploy agentscript-prod \
  --source . \
  --platform managed \
  --region us-central1 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 3600 \
  --max-instances 10 \
  --allow-unauthenticated
```

### **Oracle Cloud (Best Value)**
```bash
# SSH into Oracle Always Free compute instance
ssh ubuntu@<instance-ip>

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Clone and run
git clone https://github.com/geek-code-psj/AgentScript_fin.git
cd AgentScript_fin
docker build -t agentscript .
docker run -p 8000:8000 agentscript
```

---

## 📝 Environment Variables Needed for Any Deployment

```bash
# LLM API Keys (must provide - free options available)
OPENAI_API_KEY=sk-...      # Use free tier at openai.com
ANTHROPIC_API_KEY=sk-ant-...
TOGETHER_API_KEY=...       # Free tier available

# Observability
LANGSMITH_API_KEY=ls_...   # Optional, free tier available
LANGSMITH_ORGANIZATION_ID=...

# Database
AGENTSCRIPT_DB_PATH=/tmp/traces.db  # SQLite file

# OpenTelemetry
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

---

## 🚀 Deploy It NOW - Choose One:

### Option A: 2 Minutes (Replit)
```
1. Open https://replit.com
2. Click "Create" → "Import from GitHub"
3. Paste: https://github.com/geek-code-psj/AgentScript_fin.git
4. Run!
```

### Option B: 5 Minutes (Railway)
```
1. Open https://railway.app
2. "New Project" → "Deploy from GitHub"
3. Connect repo → Auto-deploys
4. Get public URL instantly
```

### Option C: 10 Minutes (Oracle Cloud)
```
1. Create Always Free VPS
2. ssh in, run docker commands above
3. Access public IP:8000
```

---

## 📞 Support

**Questions or issues?**
- Check docs: `docs/GETTING_STARTED.md`
- GitHub Issues: https://github.com/geek-code-psj/AgentScript_fin/issues
- Observability: Check LangSmith or OpenTelemetry dashboards

---

**AgentScript is ready to deploy. Choose your platform and go! 🚀**
