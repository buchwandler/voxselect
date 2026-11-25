use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::oneshot;

const PROTOCOL: u64 = 2;
const SERVICE: &str = "voxselect-readio-service";
const SIDECAR: &str = "binaries/voxselect-readio-service";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_FRAME_BYTES: usize = 32 * 1024 * 1024;
const MAX_LOG_BYTES: u64 = 1024 * 1024;

type SharedLog = Arc<Mutex<BackendLog>>;
type PendingRequest = oneshot::Sender<Result<Value, String>>;

struct BackendLog {
    path: PathBuf,
    file: File,
}

impl BackendLog {
    fn open(app: &AppHandle) -> Result<Self, String> {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|error| error.to_string())?;
        fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
        let path = directory.join("voxselect.log");
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|error| error.to_string())?;
        Ok(Self { path, file })
    }

    fn write_line(&mut self, message: &str) {
        let size = fs::metadata(&self.path).map(|meta| meta.len()).unwrap_or(0);
        if size > MAX_LOG_BYTES {
            let rotated = self.path.with_extension("log.1");
            let _ = self.file.flush();
            let _ = fs::remove_file(&rotated);
            if fs::rename(&self.path, &rotated).is_ok() {
                if let Ok(file) = OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&self.path)
                {
                    self.file = file;
                }
            }
        }
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_secs())
            .unwrap_or_default();
        let _ = writeln!(self.file, "{timestamp} {message}");
    }
}

fn write_log(log: &SharedLog, message: &str) {
    if let Ok(mut log) = log.lock() {
        log.write_line(message);
    }
}

#[derive(Default)]
struct JsonlDecoder {
    pending: Vec<u8>,
}

impl JsonlDecoder {
    fn push(&mut self, bytes: &[u8]) -> Result<Vec<Vec<u8>>, String> {
        self.pending.extend_from_slice(bytes);
        if self.pending.len() > MAX_FRAME_BYTES {
            return Err("Readio output exceeded the JSONL frame limit.".into());
        }
        let mut lines = Vec::new();
        while let Some(index) = self.pending.iter().position(|byte| *byte == b'\n') {
            let mut line: Vec<u8> = self.pending.drain(..=index).collect();
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            if !line.is_empty() {
                lines.push(line);
            }
        }
        Ok(lines)
    }
}

fn parse_record(line: &[u8]) -> Result<Value, String> {
    let value: Value = serde_json::from_slice(line)
        .map_err(|error| format!("Readio emitted malformed JSONL: {error}"))?;
    if !value.is_object() {
        return Err("Readio JSONL records must be objects.".into());
    }
    Ok(value)
}

fn validate_ready_record(value: &Value) -> Result<bool, String> {
    if value.get("event").and_then(Value::as_str) != Some("ready") {
        return Ok(false);
    }
    if value.get("protocol").and_then(Value::as_u64) != Some(PROTOCOL)
        || value.get("service").and_then(Value::as_str) != Some(SERVICE)
    {
        return Err("Readio sidecar reported an incompatible ready handshake.".into());
    }
    Ok(true)
}

struct Session {
    child: Option<CommandChild>,
    ready: bool,
    closing: bool,
    pending: HashMap<String, PendingRequest>,
    ready_sender: Option<oneshot::Sender<Result<(), String>>>,
}

impl Session {
    fn reject_all(&mut self, error: &str) {
        for (_, sender) in self.pending.drain() {
            let _ = sender.send(Err(error.to_owned()));
        }
        if let Some(sender) = self.ready_sender.take() {
            let _ = sender.send(Err(error.to_owned()));
        }
    }
}

#[derive(Default)]
pub struct BackendState {
    session: Mutex<Option<Arc<Mutex<Session>>>>,
    log: Mutex<Option<SharedLog>>,
    sequence: AtomicU64,
}

impl BackendState {
    fn logger(&self, app: &AppHandle) -> Result<SharedLog, String> {
        let mut current = self
            .log
            .lock()
            .map_err(|_| "Backend log lock is poisoned.".to_string())?;
        if let Some(log) = current.as_ref() {
            return Ok(log.clone());
        }
        let log = Arc::new(Mutex::new(BackendLog::open(app)?));
        *current = Some(log.clone());
        Ok(log)
    }

    fn current_session(&self) -> Result<Option<Arc<Mutex<Session>>>, String> {
        self.session
            .lock()
            .map(|session| session.clone())
            .map_err(|_| "Backend state lock is poisoned.".to_string())
    }

    pub async fn start(&self, app: AppHandle) -> Result<(), String> {
        if let Some(session) = self.current_session()? {
            let session = session
                .lock()
                .map_err(|_| "Backend session lock is poisoned.".to_string())?;
            if session.ready {
                return Ok(());
            }
        }
        self.stop();

        let log = self.logger(&app)?;
        let command = app
            .shell()
            .sidecar(SIDECAR)
            .map_err(|error| error.to_string())?
            .set_process_group(true)
            .kill_on_drop(true);
        let (mut events, child) = command.spawn().map_err(|error| error.to_string())?;
        let (ready_sender, ready_receiver) = oneshot::channel();
        let session = Arc::new(Mutex::new(Session {
            child: Some(child),
            ready: false,
            closing: false,
            pending: HashMap::new(),
            ready_sender: Some(ready_sender),
        }));
        *self
            .session
            .lock()
            .map_err(|_| "Backend state lock is poisoned.".to_string())? = Some(session.clone());
        write_log(&log, "sidecar spawned");

        let reader_app = app.clone();
        let reader_session = session.clone();
        let reader_log = log.clone();
        tauri::async_runtime::spawn(async move {
            let mut decoder = JsonlDecoder::default();
            while let Some(event) = events.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => {
                        let lines = match decoder.push(&bytes) {
                            Ok(lines) => lines,
                            Err(error) => {
                                fail_session(
                                    &reader_app,
                                    &reader_session,
                                    &reader_log,
                                    error,
                                    true,
                                );
                                return;
                            }
                        };
                        for line in lines {
                            if let Err(error) =
                                handle_record(&reader_app, &reader_session, &reader_log, &line)
                            {
                                fail_session(
                                    &reader_app,
                                    &reader_session,
                                    &reader_log,
                                    error,
                                    true,
                                );
                                return;
                            }
                        }
                    }
                    CommandEvent::Stderr(bytes) => {
                        write_log(
                            &reader_log,
                            &format!("sidecar stderr bytes={}", bytes.len()),
                        );
                    }
                    CommandEvent::Error(error) => {
                        fail_session(
                            &reader_app,
                            &reader_session,
                            &reader_log,
                            format!("Readio process stream failed: {error}"),
                            true,
                        );
                        return;
                    }
                    CommandEvent::Terminated(status) => {
                        let closing = reader_session
                            .lock()
                            .map(|session| session.closing)
                            .unwrap_or(false);
                        let message = format!(
                            "Readio service exited (code {:?}, signal {:?}).",
                            status.code, status.signal
                        );
                        fail_session(&reader_app, &reader_session, &reader_log, message, !closing);
                        return;
                    }
                    _ => {}
                }
            }
            fail_session(
                &reader_app,
                &reader_session,
                &reader_log,
                "Readio output stream closed.".into(),
                true,
            );
        });

        let readiness = tokio::time::timeout(STARTUP_TIMEOUT, ready_receiver).await;
        let startup_result = match readiness {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("Readio startup handshake was interrupted.".into()),
            Err(_) => Err("Readio service did not become ready before the startup timeout.".into()),
        };
        if let Err(error) = startup_result {
            self.stop();
            return Err(error);
        }
        if let Err(error) = self
            .request_with_timeout("ping", json!({}), REQUEST_TIMEOUT)
            .await
        {
            self.stop();
            return Err(error);
        }
        write_log(&log, "sidecar ready and ping succeeded");
        Ok(())
    }

    pub async fn request(&self, command: &str, params: Value) -> Result<Value, String> {
        self.request_with_timeout(command, params, REQUEST_TIMEOUT)
            .await
    }

    async fn request_with_timeout(
        &self,
        command: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        let session = self
            .current_session()?
            .ok_or_else(|| "Readio service is not running.".to_string())?;
        let id = format!("r{}", self.sequence.fetch_add(1, Ordering::Relaxed) + 1);
        let (sender, receiver) = oneshot::channel();
        let mut frame = json!({
            "id": id,
            "protocol": PROTOCOL,
            "command": command,
            "params": params,
        })
        .to_string();
        frame.push('\n');
        {
            let mut session = session
                .lock()
                .map_err(|_| "Backend session lock is poisoned.".to_string())?;
            if !session.ready {
                return Err("Readio service is not ready.".into());
            }
            session.pending.insert(id.clone(), sender);
            let write_result = session
                .child
                .as_mut()
                .ok_or_else(|| "Readio service is not running.".to_string())?
                .write(frame.as_bytes());
            if let Err(error) = write_result {
                session.pending.remove(&id);
                return Err(error.to_string());
            }
        }
        match tokio::time::timeout(timeout, receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("Readio response routing was interrupted.".into()),
            Err(_) => {
                if let Ok(mut session) = session.lock() {
                    session.pending.remove(&id);
                }
                Err(format!("Readio {command} request timed out."))
            }
        }
    }

    pub async fn shutdown(&self) {
        if let Ok(Some(session)) = self.current_session() {
            if let Ok(mut session) = session.lock() {
                if session.ready {
                    session.closing = true;
                }
            }
            let _ = self
                .request_with_timeout("shutdown", json!({}), Duration::from_secs(5))
                .await;
        }
        self.stop();
    }

    pub async fn restart(&self, app: AppHandle) -> Result<(), String> {
        self.shutdown().await;
        self.start(app).await
    }

    pub fn stop(&self) {
        let session = self
            .session
            .lock()
            .ok()
            .and_then(|mut current| current.take());
        if let Some(session) = session {
            if let Ok(mut session) = session.lock() {
                session.ready = false;
                session.reject_all("Readio service stopped.");
                session.child.take();
            }
        }
    }
}

fn decode_response(message: &Value) -> Result<(String, Result<Value, String>), String> {
    let Some(id) = message.get("id").and_then(Value::as_str) else {
        return Err("Readio response did not include an id or event.".into());
    };
    let result = if message.get("ok").and_then(Value::as_bool) == Some(false) {
        let error = message
            .pointer("/error/result/message")
            .and_then(Value::as_str)
            .or_else(|| message.pointer("/error/message").and_then(Value::as_str))
            .unwrap_or("Readio request failed.");
        Err(error.to_owned())
    } else {
        Ok(message.get("result").cloned().unwrap_or(Value::Null))
    };
    Ok((id.to_owned(), result))
}

fn summarize_event(message: &Value) -> String {
    let event = message
        .get("event")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    if event == "audio" {
        let bytes = message
            .get("wav_base64")
            .and_then(Value::as_str)
            .map(str::len)
            .unwrap_or_default();
        let generation = message
            .get("generation")
            .and_then(Value::as_u64)
            .unwrap_or_default();
        format!("audio event generation={generation} encoded_bytes={bytes}")
    } else {
        format!("sidecar event={event}")
    }
}

fn handle_record(
    app: &AppHandle,
    session: &Arc<Mutex<Session>>,
    log: &SharedLog,
    line: &[u8],
) -> Result<(), String> {
    let message = parse_record(line)?;
    if validate_ready_record(&message)? {
        let mut session = session
            .lock()
            .map_err(|_| "Backend session lock is poisoned.".to_string())?;
        session.ready = true;
        if let Some(sender) = session.ready_sender.take() {
            let _ = sender.send(Ok(()));
        }
        write_log(log, "sidecar protocol handshake accepted");
        return Ok(());
    }
    if message.get("protocol").and_then(Value::as_u64) != Some(PROTOCOL) {
        return Err("Readio response used an incompatible protocol version.".into());
    }
    if message.get("event").and_then(Value::as_str).is_some() {
        write_log(log, &summarize_event(&message));
        app.emit("readio-event", message)
            .map_err(|error| error.to_string())?;
        return Ok(());
    }
    let (id, result) = decode_response(&message)?;
    let sender = session
        .lock()
        .map_err(|_| "Backend session lock is poisoned.".to_string())?
        .pending
        .remove(&id);
    if let Some(sender) = sender {
        if result.is_err() {
            write_log(log, "sidecar request failed");
        }
        let _ = sender.send(result);
    }
    Ok(())
}

fn fail_session(
    app: &AppHandle,
    session: &Arc<Mutex<Session>>,
    log: &SharedLog,
    error: String,
    notify: bool,
) {
    if let Ok(mut session) = session.lock() {
        session.ready = false;
        session.reject_all(&error);
        session.child.take();
    }
    write_log(log, "sidecar session ended");
    if notify {
        let _ = app.emit("backend-error", error);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        decode_response, parse_record, summarize_event, validate_ready_record, BackendLog,
        JsonlDecoder, Session, MAX_LOG_BYTES, PROTOCOL, SERVICE,
    };
    use serde_json::json;
    use std::collections::HashMap;
    use std::fs::{self, OpenOptions};
    use std::sync::mpsc;
    use std::thread;
    use std::time::{SystemTime, UNIX_EPOCH};
    use tokio::sync::oneshot;

    #[cfg(unix)]
    use std::io::{BufRead, BufReader, Write};
    #[cfg(unix)]
    use std::process::{Command, Stdio};

    #[test]
    fn jsonl_decoder_frames_partial_and_multiple_records() {
        let mut decoder = JsonlDecoder::default();
        assert!(decoder.push(b"{\"id\":1}").unwrap().is_empty());
        assert_eq!(
            decoder.push(b"\n{\"id\":2}\n").unwrap(),
            vec![b"{\"id\":1}".to_vec(), b"{\"id\":2}".to_vec()]
        );
    }

    #[test]
    fn malformed_and_non_object_records_are_rejected() {
        assert!(parse_record(b"not json").is_err());
        assert!(parse_record(b"[]").is_err());
    }

    #[test]
    fn ready_handshake_requires_matching_protocol_and_service() {
        let ready = json!({"event":"ready", "protocol":PROTOCOL, "service":SERVICE});
        assert!(validate_ready_record(&ready).unwrap());
        let wrong_service = json!({"event":"ready", "protocol":PROTOCOL, "service":"other"});
        assert!(validate_ready_record(&wrong_service).is_err());
        let not_ready = json!({"event":"audio", "protocol":PROTOCOL});
        assert!(!validate_ready_record(&not_ready).unwrap());
    }
    #[test]
    fn responses_keep_request_ids_and_return_payloads() {
        let (id, result) = decode_response(&json!({
            "id": "r7",
            "ok": true,
            "result": {"generation": 4},
        }))
        .unwrap();
        assert_eq!(id, "r7");
        assert_eq!(result.unwrap(), json!({"generation": 4}));
    }

    #[test]
    fn responses_surface_sidecar_errors_and_require_ids() {
        let (id, result) = decode_response(&json!({
            "id": "r8",
            "ok": false,
            "error": {"result": {"message": "invalid settings"}},
        }))
        .unwrap();
        assert_eq!(id, "r8");
        assert_eq!(result.unwrap_err(), "invalid settings");
        assert!(decode_response(&json!({"ok": true})).is_err());
    }
    #[test]
    fn failed_session_rejects_pending_requests_and_startup_waiters() {
        let (pending_sender, pending_receiver) = oneshot::channel();
        let (ready_sender, ready_receiver) = oneshot::channel();
        let mut session = Session {
            child: None,
            ready: false,
            closing: false,
            pending: HashMap::from([("r1".to_owned(), pending_sender)]),
            ready_sender: Some(ready_sender),
        };
        session.reject_all("sidecar exited");
        assert_eq!(
            pending_receiver.blocking_recv().unwrap().unwrap_err(),
            "sidecar exited"
        );
        assert_eq!(
            ready_receiver.blocking_recv().unwrap().unwrap_err(),
            "sidecar exited"
        );
    }

    #[test]
    fn fake_sidecar_completes_handshake_and_correlated_request() {
        let (request_sender, request_receiver) = mpsc::channel::<Vec<u8>>();
        let (response_sender, response_receiver) = mpsc::channel::<Vec<u8>>();
        let fake_sidecar = thread::spawn(move || {
            let ready = json!({"event":"ready", "protocol":PROTOCOL, "service":SERVICE});
            response_sender
                .send(format!("{ready}\n").into_bytes())
                .unwrap();
            let request = request_receiver.recv().unwrap();
            let request: serde_json::Value =
                serde_json::from_slice(request.strip_suffix(b"\n").unwrap()).unwrap();
            assert_eq!(request["protocol"], PROTOCOL);
            assert_eq!(request["command"], "ping");
            let response = json!({
                "id": request["id"],
                "protocol": PROTOCOL,
                "ok": true,
                "result": {"service": SERVICE},
            });
            response_sender
                .send(format!("{response}\n").into_bytes())
                .unwrap();
        });

        let mut decoder = JsonlDecoder::default();
        let ready_lines = decoder.push(&response_receiver.recv().unwrap()).unwrap();
        let ready = parse_record(&ready_lines[0]).unwrap();
        assert!(validate_ready_record(&ready).unwrap());

        let (pending_sender, pending_receiver) = oneshot::channel();
        let mut pending = HashMap::from([("r42".to_owned(), pending_sender)]);
        let request = json!({"id":"r42", "protocol":PROTOCOL, "command":"ping", "params":{}});
        request_sender
            .send(format!("{request}\n").into_bytes())
            .unwrap();
        let response_lines = decoder.push(&response_receiver.recv().unwrap()).unwrap();
        let response = parse_record(&response_lines[0]).unwrap();
        assert_eq!(response["protocol"], PROTOCOL);
        let (id, result) = decode_response(&response).unwrap();
        pending.remove(&id).unwrap().send(result).unwrap();
        assert_eq!(
            pending_receiver.blocking_recv().unwrap().unwrap(),
            json!({"service": SERVICE})
        );
        fake_sidecar.join().unwrap();
    }

    #[test]
    fn backend_log_rotates_an_oversized_file() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!("voxselect-log-{stamp}"));
        fs::create_dir(&directory).unwrap();
        let path = directory.join("voxselect.log");
        fs::write(&path, vec![b'x'; MAX_LOG_BYTES as usize + 1]).unwrap();
        let file = OpenOptions::new().append(true).open(&path).unwrap();
        let mut log = BackendLog {
            path: path.clone(),
            file,
        };
        log.write_line("rotated log entry");
        assert_eq!(
            fs::metadata(path.with_extension("log.1")).unwrap().len(),
            MAX_LOG_BYTES + 1
        );
        assert!(fs::read_to_string(&path)
            .unwrap()
            .contains("rotated log entry"));
        drop(log);
        fs::remove_dir_all(directory).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn fake_sidecar_process_exits_after_correlated_shutdown() {
        let script = r#"
            printf '%s\n' '{"event":"ready","protocol":2,"service":"voxselect-readio-service"}'
            IFS= read -r request
            printf '%s\n' '{"id":"r1","protocol":2,"ok":true,"result":{"pong":true}}'
            IFS= read -r request
            printf '%s\n' '{"id":"r2","protocol":2,"ok":true,"result":{"shutdown":true}}'
        "#;
        let mut child = Command::new("sh")
            .arg("-c")
            .arg(script)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let mut stdin = child.stdin.take().unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).unwrap();
        let ready = parse_record(line.trim_end().as_bytes()).unwrap();
        assert!(validate_ready_record(&ready).unwrap());

        stdin
            .write_all(b"{\"id\":\"r1\",\"protocol\":2,\"command\":\"ping\",\"params\":{}}\n")
            .unwrap();
        line.clear();
        stdout.read_line(&mut line).unwrap();
        let ping = parse_record(line.trim_end().as_bytes()).unwrap();
        assert_eq!(
            decode_response(&ping).unwrap().1.unwrap(),
            json!({"pong": true})
        );

        stdin
            .write_all(b"{\"id\":\"r2\",\"protocol\":2,\"command\":\"shutdown\",\"params\":{}}\n")
            .unwrap();
        line.clear();
        stdout.read_line(&mut line).unwrap();
        let shutdown = parse_record(line.trim_end().as_bytes()).unwrap();
        assert_eq!(
            decode_response(&shutdown).unwrap().1.unwrap(),
            json!({"shutdown": true})
        );
        drop(stdin);
        assert!(child.wait().unwrap().success());
    }
    #[test]
    fn event_logs_omit_selection_text_and_audio_payloads() {
        let audio_summary = summarize_event(&json!({
            "event": "audio",
            "generation": 12,
            "wav_base64": "RAWAUDIO",
            "text": "selected text must not be logged",
        }));
        assert_eq!(audio_summary, "audio event generation=12 encoded_bytes=8");
        assert!(!audio_summary.contains("RAWAUDIO"));
        assert!(!audio_summary.contains("selected text"));

        let progress_summary = summarize_event(&json!({
            "event": "progress",
            "message": "selected text must not be logged",
        }));
        assert_eq!(progress_summary, "sidecar event=progress");
        assert!(!progress_summary.contains("selected text"));
    }
}
