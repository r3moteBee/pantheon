use std::net::TcpListener;
use std::process::{Command, Child, Stdio};
use std::sync::{Mutex, Arc};
use tauri::{Manager, State};

struct BackendState {
    child: Arc<Mutex<Option<Child>>>,
    port: u16,
}

fn find_open_port(start: u16) -> u16 {
    for port in start..65535 {
        if TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return port;
        }
    }
    start
}

#[tauri::command]
fn get_backend_port(state: State<'_, BackendState>) -> u16 {
    state.port
}

fn find_sidecar_path(app_handle: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    let resource_dir = app_handle.path().resource_dir().ok()?;
    if let Ok(entries) = std::fs::read_dir(resource_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                if let Some(file_name) = path.file_name().and_then(|f| f.to_str()) {
                    if file_name.starts_with("pantheon-backend") {
                        return Some(path);
                    }
                }
            }
        }
    }
    None
}

fn spawn_backend(app: &tauri::App, port: u16) -> Option<Child> {
    let app_handle = app.handle();
    
    // Resolve standard directories
    let config_dir = app_handle.path().app_config_dir().unwrap();
    let data_dir = app_handle.path().app_data_dir().unwrap();
    let log_dir = app_handle.path().app_log_dir().unwrap();
    
    std::fs::create_dir_all(&config_dir).ok();
    std::fs::create_dir_all(&data_dir).ok();
    std::fs::create_dir_all(&log_dir).ok();
    
    let env_path = config_dir.join(".env");
    if !env_path.exists() {
        // Copy .env.example if exists locally
        let root_env_example = std::path::Path::new("../.env.example");
        if root_env_example.exists() {
            std::fs::copy(root_env_example, &env_path).ok();
        } else {
            std::fs::write(&env_path, "APP_ENV=production\n").ok();
        }
    }
    
    let is_debug = cfg!(debug_assertions);
    let mut cmd = if is_debug {
        // Dev mode: spawn via local python
        let python_path = std::path::Path::new("./.venv/bin/python");
        let mut c = Command::new(python_path);
        c.args(&["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", &port.to_string(), "--log-level", "info"]);
        c.current_dir("backend");
        c
    } else {
        // Production mode: spawn packaged sidecar
        if let Some(sidecar_path) = find_sidecar_path(app_handle) {
            let mut c = Command::new(sidecar_path);
            c.args(&["--host", "127.0.0.1", "--port", &port.to_string(), "--log-level", "info"]);
            c
        } else {
            eprintln!("Sidecar binary not found in resource directory!");
            return None;
        }
    };

    cmd.env("DATA_DIR", data_dir.to_string_lossy().to_string());
    cmd.env("PORT", port.to_string());
    cmd.env("APP_ENV", if is_debug { "development" } else { "production" });
    cmd.env("PANTHEON_ENV_FILE", env_path.to_string_lossy().to_string());
    
    let log_file_path = log_dir.join("backend.log");
    if let Ok(file) = std::fs::OpenOptions::new().create(true).append(true).open(log_file_path) {
        cmd.stdout(Stdio::from(file.try_clone().unwrap()));
        cmd.stderr(Stdio::from(file));
    } else {
        cmd.stdout(Stdio::null());
        cmd.stderr(Stdio::null());
    }

    match cmd.spawn() {
        Ok(child) => {
            println!("Spawned backend process successfully on port {}", port);
            Some(child)
        }
        Err(e) => {
            eprintln!("Failed to spawn backend process: {}", e);
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = find_open_port(8000);
    let child_handle = Arc::new(Mutex::new(None));
    let child_handle_clone = Arc::clone(&child_handle);

    let mut app = tauri::Builder::default()
        .setup(move |app| {
            if let Some(child) = spawn_backend(app, port) {
                *child_handle_clone.lock().unwrap() = Some(child);
            }
            Ok(())
        })
        .manage(BackendState {
            child: Arc::clone(&child_handle),
            port,
        })
        .invoke_handler(tauri::generate_handler![get_backend_port])
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(move |_app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            let mut guard = child_handle.lock().unwrap();
            if let Some(mut child) = guard.take() {
                println!("Terminating backend process...");
                let _ = child.kill();
            }
        }
    });
}
