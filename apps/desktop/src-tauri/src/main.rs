#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[derive(serde::Serialize)]
struct Connection {
    url: String,
    token: String,
}

#[tauri::command]
fn worker_connection() -> Connection {
    Connection {
        url: "http://127.0.0.1:8765".into(),
        token: std::env::var("VENTURI_API_TOKEN").unwrap_or_default(),
    }
}

#[tauri::command]
fn client_diagnostic(message: String) {
    eprintln!(
        "Venturi client: {}",
        message.chars().take(3000).collect::<String>()
    );
}

fn main() {
    #[cfg(target_os = "linux")]
    {
        let is_wsl = std::fs::read_to_string("/proc/sys/kernel/osrelease")
            .unwrap_or_default()
            .to_lowercase()
            .contains("microsoft");
        if is_wsl && std::env::var("VENTURI_HARDWARE_RENDERING").as_deref() != Ok("1") {
            // WSL's D3D/Mesa path can silently produce an empty WebGL framebuffer.
            // M0 chooses the verified software path; hardware stays opt-in for qualification.
            for (key, value) in [
                ("LIBGL_ALWAYS_SOFTWARE", "1"),
                ("GALLIUM_DRIVER", "llvmpipe"),
                ("GDK_BACKEND", "x11"),
                ("WEBKIT_DISABLE_DMABUF_RENDERER", "1"),
            ] {
                if std::env::var_os(key).is_none() {
                    std::env::set_var(key, value);
                }
            }
            eprintln!("Venturi: using the verified WSL software rendering path.");
        }
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            worker_connection,
            client_diagnostic
        ])
        .run(tauri::generate_context!())
        .expect("Could not start the Venturi desktop window");
}
