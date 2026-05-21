# aequilibri POC — AWS EC2 Windows Server Installation Guide

This guide walks you through deploying the aequilibri web app on an AWS EC2 instance running **Windows Server 2022** (or 2019). No prior server administration experience required.

---

## What You Will Have When Done

- The aequilibri web app running at `http://YOUR-EC2-IP:8000`
- UC1 Roofing Estimator with Google Solar API, 3D model, and AI roof drawing
- UC2 Didi AI Project Assistant
- UC3 MSME Coordination module
- Django Admin panel at `http://YOUR-EC2-IP:8000/admin` (login: `admin` / `admin`)

---

## Prerequisites

Before starting, have these ready:

| Item | Where to get it |
|------|----------------|
| AWS account | aws.amazon.com |
| Google Maps API key (with Maps JS, Places, Geocoding, Solar APIs enabled) | console.cloud.google.com |
| Anthropic API key | console.anthropic.com |
| The `aequilibri_deploy` folder (this folder) | Provided by your developer |

---

## Part 1 — Launch the EC2 Instance

### 1.1 Create the Instance

1. Log into **AWS Console** → **EC2** → **Launch Instance**
2. **Name:** `aequilibri-poc`
3. **AMI:** Choose `Microsoft Windows Server 2022 Base`
4. **Instance type:** `t3.small` (minimum) — `t3.medium` recommended for demos
5. **Key pair:** Create a new key pair named `aequilibri-key` → Download the `.pem` file → **Keep it safe**
6. **Network settings:** Click **Edit**
   - Allow RDP (port 3389) from **My IP** only
   - Click **Add security group rule**:
     - Type: `Custom TCP`
     - Port range: `8000`
     - Source: `0.0.0.0/0` (allows public access to the web app)
7. **Storage:** 30 GB (default is fine)
8. Click **Launch instance**

### 1.2 Get Your EC2 Password

1. In EC2 console → **Instances** → select your instance → **Connect**
2. Click **RDP client** tab → **Get password**
3. Upload your `.pem` key file → **Decrypt password**
4. Copy the password — you will need it to log in

### 1.3 Connect via RDP

1. Note the **Public IPv4 address** from the instance summary (e.g. `54.12.34.56`)
2. On your local machine:
   - **Windows:** Open **Remote Desktop Connection** → enter the EC2 IP → connect
   - **Mac:** Install **Microsoft Remote Desktop** from App Store → add PC
3. Login with:
   - Username: `Administrator`
   - Password: the one you decrypted above
4. You are now inside the Windows Server desktop

---

## Part 2 — Install Python

1. Inside the RDP session, open **Microsoft Edge**
2. Go to: `https://www.python.org/downloads/release/python-3119/`
3. Download **Windows installer (64-bit)**
4. Run the installer:
   - ✅ **Add Python to PATH** ← critical, tick this
   - Click **Install Now**
5. Verify in **Command Prompt** (search for `cmd` in Start):
   ```
   python --version
   ```
   Should show `Python 3.11.x`

---

## Part 3 — Upload the Project Files

### Option A — Using Windows File Explorer (RDP clipboard)

1. On your local machine, zip the `aequilibri_deploy` folder
2. In the RDP session, paste the zip file (drag-and-drop often works via RDP)
3. Extract to `C:\aequilibri`

### Option B — Using AWS S3 (recommended for larger uploads)

1. On your local machine, upload `aequilibri_deploy.zip` to an S3 bucket
2. In the RDP session, open PowerShell and run:
   ```powershell
   # Install AWS CLI if not present (one-time)
   msiexec.exe /i https://awscli.amazonaws.com/AWSCLIV2.msi /qn

   # Download and extract
   aws s3 cp s3://YOUR-BUCKET/aequilibri_deploy.zip C:\aequilibri_deploy.zip
   Expand-Archive C:\aequilibri_deploy.zip -DestinationPath C:\aequilibri
   ```

### Option C — Using WinSCP + Your Key File

1. Download **WinSCP** (winscp.net) on your local machine
2. Connect using SFTP with your EC2 IP, username `Administrator`, and the `.pem` key
3. Drag the `aequilibri_deploy` folder to `C:\aequilibri` on the server

---

## Part 4 — Run the Installer

1. In the RDP session, open **PowerShell as Administrator**:
   - Right-click the Start button → **Windows PowerShell (Admin)**

2. Navigate to the project folder:
   ```powershell
   cd C:\aequilibri
   ```

3. Allow PowerShell scripts to run (one-time):
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   ```

4. Run the installer:
   ```powershell
   .\install.ps1
   ```

5. The installer will:
   - Create a Python virtual environment
   - Install all dependencies
   - Open **Notepad** with the `.env` file for you to fill in

---

## Part 5 — Configure the .env File

The installer will open `.env` in Notepad. Fill in every value:

```ini
# Generate a secret key at: https://djecrety.ir/
DJANGO_SECRET_KEY=paste-a-long-random-string-here

DJANGO_DEBUG=False

# Your EC2 Public IP (find it in AWS Console → Instances)
DJANGO_ALLOWED_HOSTS=54.12.34.56,localhost

# Your Google Maps API key
GOOGLE_MAPS_API_KEY=AIzaSy...

# Same key or a separate Solar API key
GOOGLE_SOLAR_API_KEY=AIzaSy...

# Your Anthropic key (or leave blank for demo mode)
ANTHROPIC_API_KEY=sk-ant-...

APP_PORT=8000
```

Save and close Notepad.

### Re-run the installer to finish setup:

```powershell
.\install.ps1
```

This time it will complete all steps (migrations, seed data, static files).

---

## Part 6 — Start the Server

### Foreground mode (recommended for first test):
```powershell
.\start_server.ps1
```

Open your browser to: **`http://YOUR-EC2-IP:8000`**

You should see the aequilibri home page.

### Background mode (keep running after closing PowerShell):
```powershell
.\start_server_background.ps1
```

To stop it:
```powershell
.\stop_server.ps1
```

Logs are saved to `C:\aequilibri\logs\server.log`

---

## Part 7 — Auto-Start on Server Reboot (Optional)

To make the app start automatically when Windows reboots:

1. Open **Task Scheduler** (search in Start menu)
2. Click **Create Task**
3. **General** tab:
   - Name: `aequilibri Server`
   - Select **Run whether user is logged on or not**
   - Select **Run with highest privileges**
4. **Triggers** tab → **New** → Begin task: **At startup**
5. **Actions** tab → **New**:
   - Program: `powershell.exe`
   - Arguments: `-NonInteractive -File "C:\aequilibri\start_server_background.ps1"`
6. Click **OK** → enter your Administrator password when prompted

---

## Part 8 — Verify Everything Works

Open a browser to `http://YOUR-EC2-IP:8000` and check:

| URL | Expected result |
|-----|----------------|
| `/` | aequilibri home page |
| `/uc1/` | Roofing Estimator dashboard |
| `/uc1/quotes/new/` | New Quote page with map |
| `/uc2/` | Didi AI Assistant |
| `/uc3/` | MSME Project dashboard |
| `/admin/` | Django admin (admin / admin) |

### Test the map features:
1. Go to `/uc1/quotes/new/`
2. Type a Queensland address in the search box
3. You should see the map zoom in and a blue polygon appear
4. After ~2 seconds a green building outline should overlay it
5. Click **View Roof Analysis** → 3D model should render
6. Click **📐 Roof Plan** → 2D overhead plan should render

---

## Troubleshooting

### "DisallowedHost" error
Your EC2 IP is not in `DJANGO_ALLOWED_HOSTS`. Edit `.env`, add your IP, restart server.

### Map is blank / no polygon
`GOOGLE_MAPS_API_KEY` is missing or the Maps JavaScript API is not enabled in Google Cloud Console.

### Solar API returns no data
Enable the **Solar API** in Google Cloud Console for your project.

### AI features return "Demo mode"
`ANTHROPIC_API_KEY` is missing or invalid. Check `.env`.

### Port 8000 not accessible from browser
Check EC2 Security Group → Inbound Rules → Port 8000 must allow `0.0.0.0/0`.

### Static files (CSS/images) not loading
Run: `venv\Scripts\python manage.py collectstatic --noinput`

### Server crashes on start
Check `logs\server_err.log` for the error message.

---

## Updating the App

When you receive an updated version of the project files:

1. Stop the server: `.\stop_server.ps1`
2. Replace the project files (keep your `.env` file — do not overwrite it)
3. Run: `venv\Scripts\python manage.py migrate`
4. Run: `venv\Scripts\python manage.py collectstatic --noinput`
5. Start the server: `.\start_server.ps1`

---

## Security Notes for Production

- Change the Django Admin password immediately: `/admin/` → Users → admin → Change password
- Rotate `DJANGO_SECRET_KEY` if anyone else has seen it
- Consider setting up an Elastic IP in AWS so your IP address does not change on reboot
- For HTTPS, use an AWS Application Load Balancer with an ACM certificate (optional, for future)

---

## File Structure Reference

```
C:\aequilibri\
├── aequilibri\         Django project config (settings, urls, wsgi)
├── core\               Shared Claude API client
├── uc1_roofing\        UC1 Roofing Estimator app
├── uc2_didi\           UC2 Didi AI Assistant app
├── uc3_msme\           UC3 MSME Coordination app
├── templates\          Shared HTML templates
├── staticfiles\        Compiled static files (created by collectstatic)
├── db.sqlite3          SQLite database (created on first run)
├── .env                Your secrets (DO NOT SHARE)
├── requirements.txt    Python dependencies
├── serve.py            Waitress WSGI entry point
├── install.ps1         One-time setup script
├── start_server.ps1    Start server (foreground)
├── start_server_background.ps1  Start server (background)
└── stop_server.ps1     Stop background server
```

---

*aequilibri POC — for assistance contact your developer.*
