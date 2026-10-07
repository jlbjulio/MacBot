use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Component as PathComponent, Path, PathBuf},
    process::Command,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::{Duration, Instant},
};
const MANIFEST: &str = include_str!("../../build/bootstrap-payload/manifest.json");
pub fn verify_package(root: &Path) -> Result<(), String> {
    let expected = Sha256::digest(MANIFEST.as_bytes());
    let actual = fs::read(root.join("bootstrap/manifest.json")).map_err(|e| e.to_string())?;
    if Sha256::digest(&actual) != expected {
        return Err(
            "The executable and preparation payload do not match. Rebuild the portable.".into(),
        );
    }
    Ok(())
}
#[derive(Clone, Serialize, Default)]
pub struct Progress {
    pub status: String,
    pub label: String,
    pub completed: u64,
    pub total: u64,
    pub error: String,
}
#[derive(Default)]
pub struct Bootstrap {
    pub progress: Mutex<Progress>,
    pub active: AtomicBool,
    pub stop: AtomicBool,
}
impl Bootstrap {
    pub fn phase(&self, status: &str, completed: u64, total: u64, error: &str) {
        let mut p = self.progress.lock().unwrap();
        p.status = status.into();
        if status == "starting" {
            p.label = "Starting local AI engines".into();
        }
        p.completed = completed;
        p.total = total;
        p.error = error.into();
    }
    fn step(&self, label: &str) {
        self.progress.lock().unwrap().label = label.into();
    }
    fn interrupted(&self) -> Result<(), String> {
        if self.stop.load(Ordering::Acquire) {
            Err("paused".into())
        } else {
            Ok(())
        }
    }
}
#[derive(Deserialize)]
struct Record {
    path: String,
    bytes: u64,
    sha256: String,
}
#[derive(Deserialize)]
struct Engine {
    name: String,
    version: String,
    url: String,
    sha256: String,
    bytes: u64,
    prefix: String,
    files: Vec<Record>,
}
#[derive(Deserialize)]
struct Manifest {
    schema: u32,
    python_version: String,
    python_folder: String,
    uv_sha256: String,
    requirements_sha256: String,
    python_files: Vec<Record>,
    components: Vec<Engine>,
    payload_files: Vec<Record>,
}
fn relative(name: &str) -> Result<PathBuf, String> {
    if name.is_empty()
        || name.contains(['\\', ':'])
        || name.split('/').any(|part| {
            let device = part.split('.').next().unwrap_or("").to_ascii_uppercase();
            part.is_empty()
                || part.ends_with(['.', ' '])
                || matches!(device.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                || (device.len() == 4
                    && (device.starts_with("COM") || device.starts_with("LPT"))
                    && device.as_bytes()[3].is_ascii_digit())
        })
    {
        return Err("Invalid engine file path".into());
    }
    let path = PathBuf::from(name);
    if path
        .components()
        .any(|p| !matches!(p, PathComponent::Normal(_)))
    {
        return Err("Invalid engine file path".into());
    }
    Ok(path)
}
fn reparse_point(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .map(|m| {
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                m.file_attributes() & 0x400 != 0
            }
            #[cfg(not(windows))]
            {
                m.file_type().is_symlink()
            }
        })
        .unwrap_or(false)
}
fn owned_directory(path: &Path) -> Result<(), String> {
    if reparse_point(path) {
        return Err(
            "An engine folder is linked outside this portable. Choose a regular folder.".into(),
        );
    }
    fs::create_dir_all(path).map_err(|e| e.to_string())
}
fn target(root: &Path, name: &str) -> Result<PathBuf, String> {
    let path = relative(name)?;
    let mut current = root.to_owned();
    if reparse_point(root) {
        return Err("A preparation folder is a symbolic link.".into());
    }
    for part in path.components() {
        current.push(part);
        if reparse_point(&current) {
            return Err("A preparation file is a symbolic link.".into());
        }
    }
    Ok(current)
}
#[cfg(windows)]
fn enough_space(root: &Path, needed: u64) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    let directory: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut available = 0;
    let result = unsafe {
        windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW(
            directory.as_ptr(),
            &mut available,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    if result == 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    if available < needed {
        return Err("Not enough free space. Allow 16 GB for preparation and retry.".into());
    }
    Ok(())
}
#[cfg(not(windows))]
fn enough_space(_root: &Path, _needed: u64) -> Result<(), String> {
    Ok(())
}
fn matches(path: &Path, record: &Record, state: &Bootstrap) -> Result<bool, String> {
    state.interrupted()?;
    if reparse_point(path)
        || fs::metadata(path).map(|m| m.len()).unwrap_or(u64::MAX) != record.bytes
    {
        return Ok(false);
    }
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buf = vec![0u8; 128 * 1024];
    loop {
        state.interrupted()?;
        let count = file.read(&mut buf).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        hash.update(&buf[..count]);
    }
    Ok(format!("{:x}", hash.finalize()) == record.sha256)
}
fn copy_records(
    source: &Path,
    destination: &Path,
    files: &[Record],
    state: &Bootstrap,
) -> Result<bool, String> {
    let total = files.iter().map(|r| r.bytes).sum();
    let mut completed = 0;
    for record in files {
        let dst = target(destination, &record.path)?;
        if matches(&dst, record, state)? {
            completed += record.bytes;
            continue;
        }
        let src = match target(source, &record.path) {
            Ok(path) => path,
            Err(_) => continue,
        };
        if !matches(&src, record, state)? {
            continue;
        }
        owned_directory(dst.parent().unwrap())?;
        let partial = dst.with_file_name(format!(
            "{}.partial",
            dst.file_name().unwrap().to_string_lossy()
        ));
        if reparse_point(&partial) {
            return Err("Unexpected linked partial file.".into());
        }
        let mut input = File::open(src).map_err(|e| e.to_string())?;
        let mut output = File::create(&partial).map_err(|e| e.to_string())?;
        let mut hash = Sha256::new();
        let mut copied = 0;
        let mut buf = vec![0u8; 128 * 1024];
        loop {
            state.interrupted()?;
            let count = input.read(&mut buf).map_err(|e| e.to_string())?;
            if count == 0 {
                break;
            }
            copied += count as u64;
            if copied > record.bytes {
                return Err("A cached engine file changed while copying.".into());
            }
            hash.update(&buf[..count]);
            output.write_all(&buf[..count]).map_err(|e| e.to_string())?;
            state.phase("verifying", completed + copied, total, "");
        }
        output.sync_all().map_err(|e| e.to_string())?;
        drop(output);
        if copied != record.bytes || format!("{:x}", hash.finalize()) != record.sha256 {
            fs::remove_file(&partial).map_err(|e| e.to_string())?;
            return Err("A cached engine file changed while copying. Retry preparation.".into());
        }
        fs::rename(partial, dst).map_err(|e| e.to_string())?;
        completed += record.bytes;
    }
    for record in files {
        if !matches(&target(destination, &record.path)?, record, state)? {
            return Ok(false);
        }
    }
    Ok(true)
}
fn extract(
    archive: &Path,
    destination: &Path,
    engine: &Engine,
    state: &Bootstrap,
) -> Result<(), String> {
    let mut zip = zip::ZipArchive::new(File::open(archive).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if zip.len() > 50000 {
        return Err("Unexpected engine archive size.".into());
    }
    for record in &engine.files {
        state.interrupted()?;
        let name = format!("{}{}", engine.prefix, record.path);
        let mut entry = zip.by_name(&name).map_err(|_| {
            format!(
                "The {} archive is missing a required file: {}",
                engine.name, record.path
            )
        })?;
        if entry.size() != record.bytes
            || entry
                .unix_mode()
                .map(|m| m & 0o170000 == 0o120000)
                .unwrap_or(false)
        {
            return Err("Unexpected engine archive entry.".into());
        }
        let path = target(destination, &record.path)?;
        owned_directory(path.parent().unwrap())?;
        let mut output = File::create(&path).map_err(|e| e.to_string())?;
        let mut hash = Sha256::new();
        let mut actual = 0;
        let mut buf = vec![0u8; 128 * 1024];
        loop {
            state.interrupted()?;
            let count = entry.read(&mut buf).map_err(|e| e.to_string())?;
            if count == 0 {
                break;
            }
            actual += count as u64;
            if actual > record.bytes {
                return Err("Engine file exceeded its declared size.".into());
            }
            hash.update(&buf[..count]);
            output.write_all(&buf[..count]).map_err(|e| e.to_string())?;
        }
        if actual != record.bytes || format!("{:x}", hash.finalize()) != record.sha256 {
            return Err("An engine file failed its integrity check.".into());
        }
    }
    Ok(())
}
fn clean_environment(command: &mut Command) {
    for (key, _) in std::env::vars_os() {
        let name = key.to_string_lossy();
        if name.starts_with("UV_")
            || name.starts_with("PIP_")
            || name.starts_with("PYTHON")
            || name == "VIRTUAL_ENV"
            || name.starts_with("CONDA")
        {
            command.env_remove(key);
        }
    }
    command
        .env("UV_NO_CONFIG", "1")
        .env("UV_NO_PROGRESS", "1")
        .env("DO_NOT_TRACK", "1");
}
fn uv_command(root: &Path, cache: &Path) -> Command {
    let mut command = Command::new(root.join("uv.exe"));
    clean_environment(&mut command);
    command.env("UV_CACHE_DIR", cache).current_dir(root);
    command
}
pub fn ensure(
    root: &Path,
    data: &Path,
    state: &Bootstrap,
    run: &mut impl FnMut(&mut Command) -> Result<(), String>,
) -> Result<(), String> {
    verify_package(root)?;
    let manifest: Manifest = serde_json::from_str(MANIFEST).map_err(|e| e.to_string())?;
    if manifest.schema != 1 {
        return Err("Unsupported preparation manifest.".into());
    }
    let identity = format!("{:x}", Sha256::digest(MANIFEST.as_bytes()));
    owned_directory(&root.join("runtime"))?;
    owned_directory(data)?;
    owned_directory(&data.join("cache"))?;
    owned_directory(&data.join("logs"))?;
    let payload = root.join("bootstrap");
    for file in &manifest.payload_files {
        if !matches(&target(&payload, &file.path)?, file, state)? {
            return Err(
                "The portable payload is incomplete or changed. Extract a fresh ZIP.".into(),
            );
        }
    }
    let uv = Record {
        path: "uv.exe".into(),
        bytes: fs::metadata(root.join("uv.exe"))
            .map_err(|e| e.to_string())?
            .len(),
        sha256: manifest.uv_sha256.clone(),
    };
    if !matches(&target(root, "uv.exe")?, &uv, state)? {
        return Err("The preparation tool failed its integrity check. Extract a fresh ZIP.".into());
    }
    let prepared = root.join("runtime/.prepared");
    if fs::read_to_string(&prepared).unwrap_or_default() == identity && ready(root) {
        return Ok(());
    }
    enough_space(root, 8 * 1024u64.pow(3))?;
    let default_cache = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_default()
        .join("uv/cache");
    let cache = std::env::var_os("UV_CACHE_DIR")
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .unwrap_or_else(|| {
            if default_cache.is_dir() {
                default_cache
            } else {
                data.join("cache/uv")
            }
        });
    let python = root.join("runtime/python");
    owned_directory(&python)?;
    state.step("Checking compatible Python");
    state.phase("verifying", 0, 0, "");
    let mut sources = Vec::new();
    if let Some(source) = std::env::var_os("MACBOT_EXISTING_PYTHON_DIR") {
        sources.push(PathBuf::from(source));
    }
    if let Some(base) = std::env::var_os("UV_PYTHON_INSTALL_DIR").or_else(|| {
        std::env::var_os("APPDATA").map(|p| PathBuf::from(p).join("uv/python").into_os_string())
    }) {
        sources.push(PathBuf::from(base).join(&manifest.python_folder));
    }
    let executable = manifest
        .python_files
        .iter()
        .find(|r| r.path == "python.exe")
        .ok_or("Python executable pin is missing")?;
    let mut python_ready = matches(&python.join("python.exe"), executable, state)?
        && copy_records(&python, &python, &manifest.python_files, state)?;
    if !python_ready {
        for source in sources {
            if matches(&source.join("python.exe"), executable, state)?
                && copy_records(&source, &python, &manifest.python_files, state)?
            {
                python_ready = true;
                break;
            }
        }
    }
    if !python_ready {
        state.step("Preparing Python");
        state.phase("installing", 0, 0, "");
        let stage = root.join("runtime/.python-install");
        owned_directory(&stage)?;
        let mut command = uv_command(root, &cache);
        command
            .args([
                "python",
                "install",
                &manifest.python_version,
                "--install-dir",
            ])
            .arg(&stage)
            .args(["--no-config", "--no-bin", "--no-registry"]);
        run(&mut command)?;
        if !copy_records(
            &stage.join(&manifest.python_folder),
            &python,
            &manifest.python_files,
            state,
        )? {
            return Err(
                "The downloaded Python distribution did not match its pinned files.".into(),
            );
        }
    }
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(45))
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            let allowed = matches!(
                attempt.url().host_str(),
                Some(
                    "github.com"
                        | "release-assets.githubusercontent.com"
                        | "objects.githubusercontent.com"
                        | "nodejs.org"
                )
            );
            if attempt.previous().len() >= 5 || attempt.url().scheme() != "https" || !allowed {
                attempt.error("Untrusted engine download redirect")
            } else {
                attempt.follow()
            }
        }))
        .build()
        .map_err(|e| e.to_string())?;
    let archives = data.join("cache/bootstrap");
    owned_directory(&archives)?;
    for engine in &manifest.components {
        let destination = root.join("runtime").join(&engine.name);
        owned_directory(&destination)?;
        let marker = destination.join(".verified");
        let tag = format!("{}:{}", engine.version, engine.sha256);
        if fs::read_to_string(&marker).unwrap_or_default() == tag
            && engine.files.iter().all(|r| {
                target(&destination, &r.path)
                    .map(|p| {
                        !reparse_point(&p)
                            && fs::metadata(p).map(|m| m.len() == r.bytes).unwrap_or(false)
                    })
                    .unwrap_or(false)
            })
        {
            continue;
        }
        state.step(&format!("Checking existing {}", engine.name));
        state.phase("verifying", 0, 0, "");
        let mut candidates = Vec::new();
        if engine.name == "ollama" {
            if let Some(base) = std::env::var_os("LOCALAPPDATA") {
                candidates.push(PathBuf::from(base).join("Programs/Ollama"));
            }
        }
        if engine.name == "node" {
            for base in ["ProgramFiles", "LOCALAPPDATA"] {
                if let Some(base) = std::env::var_os(base) {
                    candidates.push(PathBuf::from(base).join("nodejs"));
                }
            }
        }
        let mut reused = false;
        for candidate in candidates {
            if copy_records(&candidate, &destination, &engine.files, state)? {
                reused = true;
                break;
            }
        }
        if !reused {
            state.step(&format!("Downloading {}", engine.name));
            state.phase("downloading", 0, engine.bytes, "");
            let partial = target(
                &archives,
                &format!("{}-{}.zip.partial", engine.name, engine.version),
            )?;
            if fs::metadata(&partial)
                .map(|m| m.len() > engine.bytes)
                .unwrap_or(false)
            {
                fs::remove_file(&partial).map_err(|e| e.to_string())?;
            }
            download(&client, &engine.url, &partial, state, engine.bytes)?;
            if let Err(error) = verify(&partial, &engine.sha256, state, engine.bytes) {
                if error != "paused" {
                    fs::remove_file(&partial).map_err(|e| e.to_string())?;
                }
                return Err(error);
            }
            state.step(&format!("Preparing {}", engine.name));
            state.phase("installing", engine.bytes, engine.bytes, "");
            extract(&partial, &destination, engine, state)?;
            fs::remove_file(partial).map_err(|e| e.to_string())?;
        }
        fs::write(marker, tag).map_err(|e| e.to_string())?;
    }
    let site = python.join("Lib/site-packages");
    owned_directory(&site)?;
    let packages = python.join(".packages-ready");
    if fs::read_to_string(&packages).unwrap_or_default() != manifest.requirements_sha256
        || ![
            "torch/__init__.py",
            "uvicorn/__init__.py",
            "piper/__init__.py",
        ]
        .iter()
        .all(|p| site.join(p).is_file())
    {
        state.step("Preparing AI libraries (compatible caches are reused)");
        state.phase("installing", 0, 0, "");
        let mut command = uv_command(root, &cache);
        command
            .args(["pip", "install", "--python"])
            .arg(python.join("python.exe"))
            .arg("--target")
            .arg(&site)
            .args([
                "--require-hashes",
                "--reinstall",
                "--no-deps",
                "--only-binary",
                ":all:",
                "--link-mode",
                "copy",
                "--index-strategy",
                "unsafe-best-match",
                "--no-config",
                "-r",
            ])
            .arg(payload.join("requirements.txt"));
        run(&mut command)?;
        fs::write(packages, &manifest.requirements_sha256).map_err(|e| e.to_string())?;
    }
    for record in &manifest.payload_files {
        if let Some(name) = record.path.strip_prefix("app/") {
            if !copy_records(
                &payload.join("app"),
                &python.join("app"),
                &[Record {
                    path: name.into(),
                    bytes: record.bytes,
                    sha256: record.sha256.clone(),
                }],
                state,
            )? {
                return Err("The app payload could not be prepared.".into());
            }
        }
        if let Some(name) = record.path.strip_prefix("site-packages/") {
            if !copy_records(
                &payload.join("site-packages"),
                &site,
                &[Record {
                    path: name.into(),
                    bytes: record.bytes,
                    sha256: record.sha256.clone(),
                }],
                state,
            )? {
                return Err("The bundled parser could not be prepared.".into());
            }
        }
    }
    for folder in [
        &python,
        &root.join("runtime/node"),
        &root.join("runtime/ollama"),
    ] {
        for file in fs::read_dir(root).map_err(|e| e.to_string())? {
            let file = file.map_err(|e| e.to_string())?;
            if file.path().extension().map(|x| x == "dll").unwrap_or(false) {
                fs::copy(
                    file.path(),
                    target(folder, &file.file_name().to_string_lossy())?,
                )
                .map_err(|e| e.to_string())?;
            }
        }
    }
    if !ready(root) {
        return Err("Engine preparation is incomplete. Retry preparation.".into());
    }
    state.interrupted()?;
    fs::write(prepared, identity).map_err(|e| e.to_string())?;
    Ok(())
}
pub fn ready(root: &Path) -> bool {
    [
        "python/python.exe",
        "python/app/desktop_entry.py",
        "python/app/model-manifest.json",
        "python/Lib/site-packages/torch/__init__.py",
        "python/Lib/site-packages/piper/__init__.py",
        "node/node.exe",
        "ollama/ollama.exe",
    ]
    .iter()
    .all(|p| root.join("runtime").join(p).is_file())
}
fn copy_progress(
    input: &mut impl Read,
    output: &mut File,
    state: &Bootstrap,
    total: u64,
    mut completed: u64,
) -> Result<u64, String> {
    let mut buffer = vec![0u8; 128 * 1024];
    loop {
        state.interrupted()?;
        let count = input.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        if completed + count as u64 > total {
            return Err("The component download is larger than expected.".into());
        }
        output
            .write_all(&buffer[..count])
            .map_err(|e| e.to_string())?;
        completed += count as u64;
        state.phase("downloading", completed, total, "");
    }
    Ok(completed)
}

fn download(
    client: &reqwest::blocking::Client,
    url: &str,
    partial: &Path,
    state: &Bootstrap,
    total: u64,
) -> Result<(), String> {
    let started = Instant::now();
    let mut stalls = 0;
    while fs::metadata(partial).map(|v| v.len()).unwrap_or(0) < total {
        state.interrupted()?;
        if started.elapsed() > Duration::from_secs(3600) {
            return Err("The download took too long. Resume when your connection is ready.".into());
        }
        let offset = fs::metadata(partial).map(|v| v.len()).unwrap_or(0);
        let response = client
            .get(url)
            .header("Range", format!("bytes={offset}-"))
            .send();
        let mut response = match response {
            Ok(value) => value,
            Err(error) => {
                stalls += 1;
                if stalls >= 3 {
                    return Err(format!("Download interrupted: {error}. Retry to resume."));
                }
                continue;
            }
        };
        let append = response.status() == reqwest::StatusCode::PARTIAL_CONTENT;
        if append {
            let range = response
                .headers()
                .get("content-range")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("");
            let prefix = format!("bytes {offset}-");
            let suffix = format!("/{total}");
            if !range.starts_with(&prefix) || !range.ends_with(&suffix) {
                return Err("The server returned an invalid download range.".into());
            }
        } else if response.status() != reqwest::StatusCode::OK {
            return Err(format!(
                "App essentials are unavailable (HTTP {}). Retry when your connection is ready.",
                response.status().as_u16()
            ));
        }
        let mut output = OpenOptions::new()
            .create(true)
            .write(true)
            .append(append)
            .truncate(!append)
            .open(partial)
            .map_err(|e| e.to_string())?;
        let result = copy_progress(
            &mut response,
            &mut output,
            state,
            total,
            if append { offset } else { 0 },
        );
        output.sync_all().map_err(|e| e.to_string())?;
        if state.stop.load(Ordering::Acquire) {
            return Err("paused".into());
        }
        let after = fs::metadata(partial).map_err(|e| e.to_string())?.len();
        if result.is_err() && after >= total {
            return result.map(|_| ());
        }
        if after <= offset {
            stalls += 1;
        } else {
            stalls = 0;
        }
        if stalls >= 3 {
            return Err("The download stopped making progress. Retry to resume.".into());
        }
    }
    Ok(())
}

fn verify(path: &Path, expected: &str, state: &Bootstrap, total: u64) -> Result<(), String> {
    state.phase("verifying", total, total, "");
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0u8; 128 * 1024];
    loop {
        state.interrupted()?;
        let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    if format!("{:x}", hash.finalize()) != expected.to_ascii_lowercase() {
        return Err(
            "The download failed its integrity check. Retry to download a clean copy.".into(),
        );
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn directory() -> PathBuf {
        let p = std::env::temp_dir().join(format!("macbot-engines-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&p).unwrap();
        p
    }
    #[test]
    fn rejects_unsafe_engine_paths() {
        for name in [
            "../outside",
            "/outside",
            "C:/outside",
            "a\\b",
            "file:stream",
            "CON.txt",
            "a/file.",
            "a/../b",
        ] {
            assert!(relative(name).is_err(), "{name}");
        }
        assert!(relative("node_modules/npm/bin/npm-cli.js").is_ok());
    }
    #[test]
    fn cached_engine_copy_checks_both_source_and_result() {
        let root = directory();
        let src = root.join("source");
        let dst = root.join("target");
        fs::create_dir(&src).unwrap();
        fs::create_dir(&dst).unwrap();
        fs::write(src.join("file"), b"good").unwrap();
        let r = Record {
            path: "file".into(),
            bytes: 4,
            sha256: format!("{:x}", Sha256::digest(b"good")),
        };
        let state = Bootstrap::default();
        assert!(copy_records(&src, &dst, &[r], &state).unwrap());
        assert_eq!(fs::read(src.join("file")).unwrap(), b"good");
        assert_eq!(fs::read(dst.join("file")).unwrap(), b"good");
        fs::write(src.join("file"), b"evil").unwrap();
        fs::remove_file(dst.join("file")).unwrap();
        let r = Record {
            path: "file".into(),
            bytes: 4,
            sha256: format!("{:x}", Sha256::digest(b"good")),
        };
        assert!(!copy_records(&src, &dst, &[r], &state).unwrap());
        assert!(!dst.join("file").exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn corrupt_download_is_rejected_and_pause_preserves_partial() {
        let root = directory();
        let p = root.join("partial");
        fs::write(&p, b"good").unwrap();
        let state = Bootstrap::default();
        assert!(verify(&p, &"0".repeat(64), &state, 4).is_err());
        state.stop.store(true, Ordering::Release);
        assert_eq!(
            verify(&p, &"0".repeat(64), &state, 4).unwrap_err(),
            "paused"
        );
        assert_eq!(fs::read(p).unwrap(), b"good");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn real_http_range_resume_preserves_existing_bytes() {
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/component", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            let mut b = [0];
            while !request.ends_with(b"\r\n\r\n") {
                socket.read_exact(&mut b).unwrap();
                request.push(b[0]);
            }
            assert!(String::from_utf8(request)
                .unwrap()
                .to_lowercase()
                .contains("range: bytes=3-"));
            socket.write_all(b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 3-4/5\r\nContent-Length: 2\r\nConnection: close\r\n\r\nde").unwrap();
        });
        let root = directory();
        let p = root.join("partial");
        fs::write(&p, b"abc").unwrap();
        let client = reqwest::blocking::Client::builder()
            .no_proxy()
            .build()
            .unwrap();
        download(&client, &url, &p, &Bootstrap::default(), 5).unwrap();
        assert_eq!(fs::read(p).unwrap(), b"abcde");
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn pinned_component_extraction_checks_file_contents() {
        let root = directory();
        let archive = root.join("component.zip");
        let mut zip = zip::ZipWriter::new(File::create(&archive).unwrap());
        zip.start_file("prefix/node.exe", zip::write::SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"good").unwrap();
        zip.finish().unwrap();
        let destination = root.join("node");
        fs::create_dir(&destination).unwrap();
        let mut engine = Engine {
            name: "node".into(),
            version: "test".into(),
            url: "unused".into(),
            sha256: "unused".into(),
            bytes: 4,
            prefix: "prefix/".into(),
            files: vec![Record {
                path: "node.exe".into(),
                bytes: 4,
                sha256: format!("{:x}", Sha256::digest(b"good")),
            }],
        };
        extract(&archive, &destination, &engine, &Bootstrap::default()).unwrap();
        assert_eq!(fs::read(destination.join("node.exe")).unwrap(), b"good");
        engine.files[0].sha256 = "0".repeat(64);
        assert!(extract(&archive, &destination, &engine, &Bootstrap::default()).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
