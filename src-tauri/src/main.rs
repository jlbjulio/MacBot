#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(all(target_os = "windows", target_arch = "x86_64")))]
compile_error!("MacBot supports Windows x64 PCs.");

use std::sync::atomic::{AtomicBool, Ordering};
use std::{
    fs,
    io::Write,
    net::TcpListener,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};
use tauri::Manager;
mod bootstrap;

#[cfg(windows)]
struct ChildJob(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
unsafe impl Send for ChildJob {}
#[cfg(windows)]
impl ChildJob {
    fn new() -> std::io::Result<Self> {
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(std::io::Error::last_os_error());
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
            {
                windows_sys::Win32::Foundation::CloseHandle(handle);
                return Err(std::io::Error::last_os_error());
            }
            Ok(Self(handle))
        }
    }
    fn attach(&self, child: &Child) -> std::io::Result<()> {
        use std::os::windows::io::AsRawHandle;
        unsafe {
            if windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(
                self.0,
                child.as_raw_handle() as _,
            ) == 0
            {
                return Err(std::io::Error::last_os_error());
            }
        }
        Ok(())
    }
}
#[cfg(windows)]
impl Drop for ChildJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct Connection {
    base_url: String,
    token: String,
}

#[derive(Default)]
struct RuntimeState {
    connection: Mutex<Option<Connection>>,
    error: Mutex<Option<String>>,
    children: Mutex<Vec<Child>>,
    closing: AtomicBool,
    #[cfg(windows)]
    job: Mutex<Option<ChildJob>>,
}

fn register_child(app: &tauri::AppHandle, mut child: Child) -> Result<(), String> {
    let state = app.state::<RuntimeState>();
    let mut children = state.children.lock().map_err(|e| e.to_string())?;
    let attach = || -> Result<(), String> {
        if state.closing.load(Ordering::Acquire) {
            return Err("Startup cancelled".into());
        }
        #[cfg(windows)]
        {
            let mut job = state.job.lock().map_err(|e| e.to_string())?;
            if job.is_none() {
                *job = Some(ChildJob::new().map_err(|e| e.to_string())?);
            }
            job.as_ref()
                .unwrap()
                .attach(&child)
                .map_err(|e| e.to_string())?;
        }
        Ok(())
    };
    if let Err(error) = attach() {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    children.push(child);
    Ok(())
}

#[tauri::command]
async fn connection(app: tauri::AppHandle) -> Result<Connection, String> {
    for _ in 0..240 {
        let state = app.state::<RuntimeState>();
        if let Some(value) = state.connection.lock().map_err(|e| e.to_string())?.clone() {
            return Ok(value);
        }
        if let Some(error) = state.error.lock().map_err(|e| e.to_string())?.clone() {
            return Err(error);
        }
        tauri::async_runtime::spawn_blocking(|| std::thread::sleep(Duration::from_millis(250)))
            .await
            .map_err(|e| e.to_string())?;
    }
    Err("The local workspace could not start. Check your MacBot logs.".into())
}

fn spawn_hidden(command: &mut Command) -> std::io::Result<Child> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command.spawn()
}

fn start_runtime(app: &tauri::AppHandle) -> Result<Connection, String> {
    if let Ok(token) = std::env::var("MACBOT_TOKEN") {
        return Ok(Connection {
            base_url: std::env::var("MACBOT_API_URL").unwrap_or("http://127.0.0.1:8765/api".into()),
            token,
        });
    }
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let root = executable
        .parent()
        .ok_or("The MacBot folder could not be found.")?;
    let portable = root.join("portable.marker").is_file();
    let directory = match std::env::var_os("MACBOT_DATA_DIR") {
        Some(path) => PathBuf::from(path),
        None if portable => root.join("Data"),
        None => app.path().app_local_data_dir().map_err(|e| e.to_string())?,
    };
    for folder in ["uploads", "artifacts", "models", "qdrant", "logs", "cache"] {
        fs::create_dir_all(directory.join(folder)).map_err(|e| e.to_string())?;
    }
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;
    let port = TcpListener::bind("127.0.0.1:0")
        .map_err(|e| e.to_string())?
        .local_addr()
        .map_err(|e| e.to_string())?
        .port();
    let token = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );
    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let sidecar = resource_dir.join("macbot-backend.exe");
    let sidecar = if sidecar.exists() {
        sidecar
    } else {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        exe.parent()
            .ok_or("The MacBot folder could not be found.")?
            .join("macbot-backend.exe")
    };
    let logfile =
        fs::File::create(directory.join("logs/backend.log")).map_err(|e| e.to_string())?;
    let python = root.join("runtime/python/python.exe");
    let mut command = Command::new(if portable { python.clone() } else { sidecar });
    let existing_hf = std::env::var_os("HF_HUB_CACHE").or_else(|| {
        std::env::var_os("HF_HOME").map(|path| PathBuf::from(path).join("hub").into_os_string())
    });
    if let Some(cache) = existing_hf {
        command.env("MACBOT_EXISTING_HF_CACHE", cache);
    }
    if portable {
        command.arg(root.join("runtime/python/app/desktop_entry.py"));
        command.env("PYTHONHOME", python.parent().unwrap());
        command.env("MACBOT_PORTABLE", "1");
    }
    command
        .args(["--port", &port.to_string(), "--parent-watch"])
        .env("MACBOT_TOKEN", &token)
        .env("MACBOT_DATA_DIR", &directory)
        .env("HF_HOME", directory.join("cache/huggingface"))
        .env("HF_HUB_CACHE", directory.join("cache/huggingface/hub"))
        .env(
            "HF_ASSETS_CACHE",
            directory.join("cache/huggingface/assets"),
        )
        .env("HF_XET_CACHE", directory.join("cache/huggingface/xet"))
        .env("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        .env("HF_HUB_DISABLE_TELEMETRY", "1")
        .env("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        .env("DO_NOT_TRACK", "1")
        .env("TOKENIZERS_PARALLELISM", "false")
        .stdin(Stdio::piped())
        .stdout(logfile.try_clone().map_err(|e| e.to_string())?)
        .stderr(logfile);
    let child = spawn_hidden(&mut command)
        .map_err(|e| format!("The local workspace could not start: {e}"))?;
    register_child(app, child)?;
    let base_url = format!("http://127.0.0.1:{port}/api");
    let start = Instant::now();
    // Cold initialization of the local AI libraries exceeded 55 seconds during native QA.
    while start.elapsed() < Duration::from_secs(180) {
        if app.state::<RuntimeState>().closing.load(Ordering::Acquire) {
            return Err("Startup cancelled".into());
        }
        if client
            .get(format!("{base_url}/health"))
            .bearer_auth(&token)
            .send()
            .map(|r| r.status().is_success())
            .unwrap_or(false)
        {
            return Ok(Connection { base_url, token });
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err(format!(
        "The local workspace did not respond. Check {}",
        directory.join("logs/backend.log").display()
    ))
}

fn stop_owned(app: &tauri::AppHandle) {
    let state = app.state::<RuntimeState>();
    if let Ok(mut children) = state.children.lock() {
        for child in children.iter_mut().rev() {
            eprintln!("Stopping owned process {}", child.id());
            if let Some(mut input) = child.stdin.take() {
                let _ = input.write_all(b"shutdown\n");
            }
            for _ in 0..20 {
                if child.try_wait().ok().flatten().is_some() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            if child.try_wait().ok().flatten().is_none() {
                let _ = child.kill();
            }
            for _ in 0..20 {
                if child.try_wait().ok().flatten().is_some() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
        children.clear();
    };
    #[cfg(windows)]
    {
        state.job.lock().unwrap().take();
    }
}

fn shutdown(app: &tauri::AppHandle) {
    app.state::<RuntimeState>()
        .closing
        .store(true, Ordering::Release);
    app.state::<bootstrap::Bootstrap>()
        .stop
        .store(true, Ordering::Release);
    stop_owned(app);
}

#[tauri::command]
fn bootstrap_status(app: tauri::AppHandle) -> bootstrap::Progress {
    app.state::<bootstrap::Bootstrap>()
        .progress
        .lock()
        .unwrap()
        .clone()
}

#[tauri::command]
fn bootstrap_pause(app: tauri::AppHandle) {
    app.state::<bootstrap::Bootstrap>()
        .stop
        .store(true, Ordering::Release);
}

#[tauri::command]
fn bootstrap_resume(app: tauri::AppHandle) {
    begin_startup(&app);
}

fn run_preparation(
    app: &tauri::AppHandle,
    data: &std::path::Path,
    command: &mut Command,
) -> Result<(), String> {
    let log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(data.join("logs/preparation.log"))
        .map_err(|e| e.to_string())?;
    command
        .stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log);
    let child = spawn_hidden(command).map_err(|e| e.to_string())?;
    let pid = child.id();
    register_child(app, child)?;
    let started = Instant::now();
    loop {
        let state = app.state::<RuntimeState>();
        let interrupted = app
            .state::<bootstrap::Bootstrap>()
            .stop
            .load(Ordering::Acquire)
            || state.closing.load(Ordering::Acquire);
        let mut children = state.children.lock().map_err(|e| e.to_string())?;
        let child = children
            .iter_mut()
            .find(|child| child.id() == pid)
            .ok_or("Preparation process closed")?;
        if interrupted || started.elapsed() > Duration::from_secs(3600) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(if interrupted {
                "paused".into()
            } else {
                "Preparation took too long. Retry to reuse completed downloads.".into()
            });
        }
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            return if status.success() {
                Ok(())
            } else {
                Err(format!(
                    "Preparation failed. Retry, or check {} for details.",
                    data.join("logs/preparation.log").display()
                ))
            };
        }
        drop(children);
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn begin_startup(app: &tauri::AppHandle) {
    let state = app.state::<bootstrap::Bootstrap>();
    if state.progress.lock().unwrap().status == "ready"
        || app.state::<RuntimeState>().closing.load(Ordering::Acquire)
        || state.active.swap(true, Ordering::AcqRel)
    {
        return;
    }
    state.stop.store(false, Ordering::Release);
    let handle = app.clone();
    std::thread::spawn(move || {
        let result = (|| -> Result<(), String> {
            let executable = std::env::current_exe().map_err(|e| e.to_string())?;
            let root = executable
                .parent()
                .ok_or("The MacBot folder could not be found.")?;
            if root.join("portable.marker").is_file() {
                let data = std::env::var_os("MACBOT_DATA_DIR")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| root.join("Data"));
                bootstrap::ensure(
                    root,
                    &data,
                    &handle.state::<bootstrap::Bootstrap>(),
                    &mut |command| run_preparation(&handle, &data, command),
                )?;
            }
            handle
                .state::<bootstrap::Bootstrap>()
                .phase("starting", 0, 0, "");
            stop_owned(&handle);
            *handle.state::<RuntimeState>().error.lock().unwrap() = None;
            let value = start_runtime(&handle)?;
            *handle.state::<RuntimeState>().connection.lock().unwrap() = Some(value);
            Ok(())
        })();
        let state = handle.state::<bootstrap::Bootstrap>();
        let previous = state.progress.lock().unwrap().clone();
        match result {
            Ok(()) => state.phase("ready", previous.total, previous.total, ""),
            Err(error) if error == "paused" => {
                state.phase("paused", previous.completed, previous.total, "")
            }
            Err(error) => state.phase("failed", previous.completed, previous.total, &error),
        }
        state.active.store(false, Ordering::Release);
    });
}

fn main() {
    if std::env::args().nth(1).as_deref() == Some("--verify-package") {
        let result = std::env::current_exe()
            .map_err(|e| e.to_string())
            .and_then(|exe| {
                bootstrap::verify_package(exe.parent().ok_or("Missing portable folder")?)
            });
        if let Err(error) = result {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .manage(RuntimeState::default())
        .manage(bootstrap::Bootstrap::default())
        .invoke_handler(tauri::generate_handler![
            connection,
            bootstrap_status,
            bootstrap_pause,
            bootstrap_resume
        ])
        .setup(|app| {
            let executable = std::env::current_exe()?;
            let root = executable
                .parent()
                .ok_or("The MacBot folder could not be found.")?;
            let config = app
                .config()
                .app
                .windows
                .first()
                .ok_or("The main window configuration is missing.")?;
            let mut window = tauri::WebviewWindowBuilder::from_config(app, config)?;
            if root.join("portable.marker").is_file() {
                let directory = std::env::var_os("MACBOT_DATA_DIR")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| root.join("Data"));
                let browser_data = directory.join("cache/webview");
                fs::create_dir_all(&browser_data)?;
                window = window.data_directory(browser_data);
            }
            window.build()?;
            begin_startup(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("MacBot could not start.");
    app.run(|handle, event| match event {
        tauri::RunEvent::WindowEvent {
            label,
            event: tauri::WindowEvent::CloseRequested { api, .. },
            ..
        } if label == "main" => {
            eprintln!("Main window requested application shutdown");
            api.prevent_close();
            if !handle
                .state::<RuntimeState>()
                .closing
                .swap(true, Ordering::AcqRel)
            {
                let handle = handle.clone();
                std::thread::spawn(move || {
                    shutdown(&handle);
                    eprintln!("Runtime stopped; requesting application exit");
                    handle.exit(0);
                });
            }
        }
        tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => shutdown(handle),
        _ => {}
    });
}
