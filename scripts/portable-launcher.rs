#![windows_subsystem = "windows"]

use std::{ffi::c_void, os::windows::process::CommandExt, path::PathBuf, process::{Command, Stdio}};

#[link(name = "advapi32")]
unsafe extern "system" {
    fn RegGetValueW(key: *mut c_void, subkey: *const u16, value: *const u16,
                    flags: u32, kind: *mut u32, data: *mut c_void, size: *mut u32) -> i32;
}
#[link(name = "user32")]
unsafe extern "system" {
    fn MessageBoxW(window: *mut c_void, text: *const u16, title: *const u16, flags: u32) -> i32;
}

fn wide(text: &str) -> Vec<u16> { text.encode_utf16().chain(Some(0)).collect() }

fn installed_python() -> Option<PathBuf> {
    for hive in [-2147483647isize, -2147483646isize] {
        for key in ["Software\\Python\\Astral\\CPython3.11.16\\InstallPath",
                    "Software\\Python\\PythonCore\\3.11\\InstallPath"] {
            let mut buffer = vec![0u16; 16384];
            let mut size = (buffer.len() * 2) as u32;
            let result = unsafe { RegGetValueW(hive as *mut c_void, wide(key).as_ptr(),
                std::ptr::null(), 2, std::ptr::null_mut(), buffer.as_mut_ptr().cast(), &mut size) };
            if result == 0 && size >= 2 && size as usize <= buffer.len() * 2 {
                let path = PathBuf::from(String::from_utf16_lossy(&buffer[..size as usize / 2 - 1]));
                if path.join("python.exe").is_file() { return Some(path); }
            }
        }
    }
    None
}

fn launch() -> Result<(), Box<dyn std::error::Error>> {
    let executable = std::env::current_exe()?;
    let support = executable.parent().ok_or("Missing portable folder")?.join("MacBot");
    let app = support.join("MacBot.exe");
    if !app.is_file() { return Err("Extract the entire ZIP and keep the MacBot folder beside MacBot.exe.".into()); }
    let mut command = Command::new(app);
    command.current_dir(&support).args(std::env::args_os().skip(1))
        .creation_flags(0x08000000).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if std::env::var_os("MACBOT_EXISTING_PYTHON_DIR").is_none() {
        if let Some(python) = installed_python() { command.env("MACBOT_EXISTING_PYTHON_DIR", python); }
    }
    if std::env::args().nth(1).as_deref() == Some("--verify-package") {
        std::process::exit(command.status()?.code().unwrap_or(1));
    }
    command.spawn()?;
    Ok(())
}

fn main() {
    if let Err(error) = launch() {
        if std::env::args().nth(1).as_deref() != Some("--verify-package") {
            unsafe { MessageBoxW(std::ptr::null_mut(), wide(&error.to_string()).as_ptr(), wide("MacBot").as_ptr(), 0x10); }
        }
        std::process::exit(1);
    }
}
