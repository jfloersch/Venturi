use std::fs;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

#[derive(Default)]
pub struct SetupState(pub Mutex<Option<Child>>);

#[derive(serde::Serialize, serde::Deserialize)]
pub struct Connection {
    pub url: String,
    pub token: String,
}

fn worker_distro() -> Result<String, String> {
    let output = Command::new("wsl.exe")
        .args(["--list", "--quiet"])
        .output()
        .map_err(|e| e.to_string())?;
    // wsl.exe emits UTF-16LE when redirected on Windows.
    let text = if output.stdout.contains(&0) {
        String::from_utf16_lossy(
            &output
                .stdout
                .chunks_exact(2)
                .map(|b| u16::from_le_bytes([b[0], b[1]]))
                .collect::<Vec<_>>(),
        )
    } else {
        String::from_utf8_lossy(&output.stdout).into_owned()
    };
    let mut names: Vec<_> = text
        .lines()
        .map(|s| s.trim().trim_start_matches('\u{feff}'))
        .filter(|s| !s.is_empty())
        .collect();
    names.sort_by_key(|name| (*name != "Ubuntu-22.04", *name != "Ubuntu", *name));
    for name in names {
        let release = Command::new("wsl.exe")
            .args(["-d", name, "--exec", "cat", "/etc/os-release"])
            .output()
            .map_err(|e| e.to_string())?;
        let release = String::from_utf8_lossy(&release.stdout);
        if release.lines().any(|line| line == "ID=ubuntu")
            && release.lines().any(|line| line == "VERSION_ID=\"22.04\"")
        {
            return Ok(name.to_string());
        }
    }
    Err("Install Ubuntu 22.04 with WSL and open it once to create your Linux account.".into())
}

fn install_root() -> Result<PathBuf, String> {
    if let Some(root) = std::env::var_os("VENTURI_INSTALL_DIR") {
        return Ok(PathBuf::from(root));
    }
    if let Some(root) = std::env::var_os("XDG_DATA_HOME") {
        return Ok(PathBuf::from(root).join("venturi"));
    }
    Ok(
        PathBuf::from(std::env::var_os("HOME").ok_or("Home directory unavailable")?)
            .join(".local/share/venturi"),
    )
}

#[tauri::command]
pub async fn worker_connection() -> Result<Connection, String> {
    if let Ok(token) = std::env::var("VENTURI_API_TOKEN") {
        if !token.is_empty() {
            return Ok(Connection {
                url: "http://127.0.0.1:8765".into(),
                token,
            });
        }
    }
    tauri::async_runtime::spawn_blocking(|| {
        let mut command;
        if cfg!(target_os = "windows") {
            let distro = worker_distro()?;
            command = Command::new("wsl.exe");
            command.args(["-d", &distro, "--exec", "bash", "-c",
                "exec \"$HOME/.local/share/venturi/current/bin/python\" -m venturi.desktop_host"]);
        } else if cfg!(target_os = "linux") {
            command = Command::new(install_root()?.join("current/bin/python"));
            command.args(["-m", "venturi.desktop_host"]);
        } else {
            return Err("Use a separately qualified Linux worker. Guided macOS/ARM setup is not yet qualified.".into());
        }
        let output = command.output().map_err(|_| "Install the local worker using Guided setup first.")?;
        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).chars().take(2000).collect());
        }
        serde_json::from_slice(&output.stdout).map_err(|_| "Worker returned an invalid connection.".into())
    }).await.map_err(|e| e.to_string())?
}

fn log_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let folder = app.path().app_log_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    Ok(folder.join("worker-setup.log"))
}

#[tauri::command]
pub fn setup_worker(app: tauri::AppHandle, state: tauri::State<SetupState>) -> Result<(), String> {
    let mut process = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(child) = process.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err("Setup is already running.".into());
        }
    }
    let script = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("worker-payload/install-worker.sh");
    if !script.is_file() {
        return Err(
            "This development binary has no worker payload. Build a release package first.".into(),
        );
    }
    let mut command;
    if cfg!(target_os = "windows") {
        let distro = worker_distro()?;
        let translated = Command::new("wsl.exe")
            .args(["-d", &distro, "--exec", "wslpath", "-a"])
            .arg(&script)
            .output()
            .map_err(|e| e.to_string())?;
        if !translated.status.success() {
            return Err("Install Ubuntu-22.04 with WSL, open it once to create your Linux account, then retry.".into());
        }
        command = Command::new("wsl.exe");
        command.args([
            "-d",
            &distro,
            "--exec",
            "bash",
            String::from_utf8_lossy(&translated.stdout).trim(),
        ]);
    } else if cfg!(target_os = "linux") {
        command = Command::new("bash");
        command.arg(script);
    } else {
        return Err("Guided setup supports Ubuntu 22.04 x86_64 and Windows WSL2.".into());
    }
    let output = fs::File::create(log_path(&app)?).map_err(|e| e.to_string())?;
    let errors = output.try_clone().map_err(|e| e.to_string())?;
    *process = Some(
        command
            .stdin(Stdio::null())
            .stdout(output)
            .stderr(errors)
            .spawn()
            .map_err(|e| e.to_string())?,
    );
    Ok(())
}

#[tauri::command]
pub fn setup_status(
    app: tauri::AppHandle,
    state: tauri::State<SetupState>,
) -> Result<serde_json::Value, String> {
    let mut process = state.0.lock().map_err(|e| e.to_string())?;
    let status = match process.as_mut() {
        Some(child) => match child.try_wait().map_err(|e| e.to_string())? {
            Some(code) => {
                if code.success() {
                    "completed"
                } else {
                    "failed"
                }
            }
            None => "running",
        },
        None => "idle",
    };
    let log = fs::read_to_string(log_path(&app)?).unwrap_or_default();
    let tail = log
        .chars()
        .rev()
        .take(6000)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    Ok(serde_json::json!({"status": status, "log": tail, "platform": std::env::consts::OS}))
}

#[tauri::command]
pub fn install_wsl() -> Result<(), String> {
    if !cfg!(target_os = "windows") {
        return Err("WSL setup is available on Windows.".into());
    }
    Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-Command",
            "Start-Process wsl.exe -Verb RunAs -ArgumentList '--install','-d','Ubuntu-22.04' -Wait",
        ])
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn install_prerequisites(
    app: tauri::AppHandle,
    state: tauri::State<SetupState>,
) -> Result<(), String> {
    let mut process = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(child) = process.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err("A setup operation is already running.".into());
        }
    }
    let mut command;
    if cfg!(target_os = "windows") {
        let distro = worker_distro()?;
        command = Command::new("wsl.exe");
        command.args(["-d", &distro, "-u", "root", "--exec", "bash", "-c",
            "apt-get update && apt-get install -y ca-certificates libstdc++6 libgomp1 libgl1 libxrender1"]);
    } else if cfg!(target_os = "linux") {
        command = Command::new("pkexec");
        command.args([
            "apt-get",
            "install",
            "-y",
            "ca-certificates",
            "libstdc++6",
            "libgomp1",
            "libgl1",
            "libxrender1",
        ]);
    } else {
        return Err("Prerequisite setup requires Ubuntu 22.04 or Windows WSL2.".into());
    }
    let output = fs::File::create(log_path(&app)?).map_err(|e| e.to_string())?;
    let errors = output.try_clone().map_err(|e| e.to_string())?;
    *process = Some(
        command
            .stdin(Stdio::null())
            .stdout(output)
            .stderr(errors)
            .spawn()
            .map_err(|e| e.to_string())?,
    );
    Ok(())
}

#[tauri::command]
pub fn open_checkout(url: String) -> Result<(), String> {
    // Never open arbitrary schemes or execute user input through a shell.
    if !url.starts_with("https://checkout.stripe.com/") || url.chars().any(char::is_control) {
        return Err("Only the secure Stripe checkout address may be opened.".into());
    }
    if cfg!(target_os = "windows") {
        Command::new("rundll32.exe")
            .args(["url.dll,FileProtocolHandler", &url])
            .spawn()
    } else if cfg!(target_os = "macos") {
        Command::new("open").arg(&url).spawn()
    } else {
        Command::new("xdg-open").arg(&url).spawn()
    }
    .map_err(|e| e.to_string())?;
    Ok(())
}
