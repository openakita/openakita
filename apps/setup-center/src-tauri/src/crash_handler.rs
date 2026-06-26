//! Native crash handler that captures minidumps for SEH exceptions
//! (0xc0000005 access violation / 0xc000001d illegal instruction / etc.)
//! which `std::panic::set_hook` cannot see.
//!
//! ## What this handler does NOT catch
//!
//! `SetUnhandledExceptionFilter` only runs for exceptions dispatched
//! through normal SEH. **Fast-fail terminations bypass it entirely**:
//! heap-corruption detection (0xc0000374), `/GS` stack-cookie failures,
//! and Control Flow Guard violations all funnel through
//! `__fastfail` / `RtlFailFast`, which raises a non-continuable,
//! non-dispatchable exception (int 0x29) that skips both vectored handlers
//! and this top-level filter and goes straight to Windows Error Reporting.
//! So a heap-corruption crash produces **no** `*.dmp` here — its evidence
//! lands in WER instead, which is why the feedback bundle separately
//! collects WER `Report.wer` / minidumps (from both `ReportQueue` and
//! `ReportArchive`) and WebView2's own Crashpad dumps. Likewise a crash
//! inside the WebView2 process is in a different process and is captured by
//! Edge's Crashpad, not by this filter.
//!
//! Design notes:
//!
//! * We do **not** touch HKLM `\Software\Microsoft\Windows\Windows Error
//!   Reporting\LocalDumps` because that requires Administrator. Instead we
//!   own the lifecycle entirely in-process: `SetUnhandledExceptionFilter`
//!   installs our callback and `MiniDumpWriteDump` writes a normal-sized
//!   (~1-5 MB) dump into `~/.openakita/crashdumps/`.
//! * The callback must be signal-safe: no heap allocations, no locks, no
//!   `String::from_utf8` etc. We pre-compute the dump directory at install
//!   time and store a wide-char prefix in a `OnceLock`; the callback copies
//!   that prefix into a fixed stack buffer, appends
//!   `\openakita-{pid}-{ticks}.dmp`, opens the file via
//!   `CreateFileW`, dumps, and chains to the prior filter.
//! * Retention is best-effort and runs at install time (not inside the
//!   crash callback): we list `crashdumps/`, sort by mtime descending, and
//!   `remove_file` everything past index 5.
//!
//! No-op on non-Windows targets. The feedback bundle (`build_feedback_zip`)
//! looks for `~/.openakita/crashdumps/*.dmp` regardless of platform, so
//! macOS / Linux users still get their existing `crash.log` shipped, just
//! without binary dumps (which they wouldn't have anyway).
//!
//! ## Events ring buffer
//!
//! In addition to the SEH minidump, this module owns a 64-slot ring buffer
//! of recent diagnostic events (each entry is one line of text plus a unix
//! timestamp). Code anywhere in the desktop crate calls `record_event(...)`
//! during normal operation; `log_to_file` is wired to forward every line it
//! writes, so any path that already logs to `autostart.log` automatically
//! shows up in the ring buffer without further changes.
//!
//! The ring buffer is read in two places:
//!
//! 1. The Rust panic hook reads it via `snapshot_events()` and embeds the
//!    last 64 events into `crash.log`, so a panic-class crash arrives with
//!    a built-in "what was happening just before" trail next to the source
//!    location, payload, machine fingerprint, and backtrace.
//! 2. The native SEH crash filter reads it via `try_lock` (best-effort,
//!    never blocks) and writes a sibling `<pid>-<tick>.events.txt` next to
//!    the minidump, so SEH-class crashes — which never trigger the Rust
//!    panic hook — also ship with their own event trail.
//!
//! The SEH path uses `try_lock` rather than `lock` because by the time we
//! land in the unhandled exception filter the heap may already be corrupt
//! (e.g. 0xc0000374) and waiting for a poisoned mutex would mean producing
//! no diagnostic at all instead of a partial one.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::Mutex;

use once_cell::sync::Lazy;

const EVENT_RING_CAPACITY: usize = 64;
const EVENT_MAX_LEN: usize = 512;

static EVENTS_RING: Lazy<Mutex<VecDeque<String>>> =
    Lazy::new(|| Mutex::new(VecDeque::with_capacity(EVENT_RING_CAPACITY)));

/// Push a single event line into the ring buffer. Best-effort: if another
/// thread already holds the lock we drop the event rather than block,
/// because every caller of `record_event` is in the critical path of UI /
/// IPC / tray callbacks where blocking on a logging mutex would itself
/// produce the kind of hang we're trying to diagnose.
pub fn record_event(msg: &str) {
    let body = if msg.len() > EVENT_MAX_LEN {
        // `msg.len()` is a BYTE count; slicing `&msg[..EVENT_MAX_LEN]` panics
        // if that byte falls inside a multi-byte UTF-8 char (the common case
        // for CJK log content). Back off to the nearest char boundary at or
        // below the limit. This must never panic: `record_event` is forwarded
        // every `log_to_file` line and runs inside UI / IPC / tray callbacks,
        // where a panic can unwind across an `extern "system"` FFI frame and
        // abort the whole process — the exact crash class this module exists
        // to diagnose, not cause.
        let mut end = EVENT_MAX_LEN;
        while end > 0 && !msg.is_char_boundary(end) {
            end -= 1;
        }
        format!("{}\u{2026}[+{} bytes]", &msg[..end], msg.len() - end)
    } else {
        msg.to_string()
    };
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Ok(mut g) = EVENTS_RING.try_lock() {
        if g.len() >= EVENT_RING_CAPACITY {
            g.pop_front();
        }
        g.push_back(format!("[{ts}] {body}"));
    }
}

/// Drain the current ring buffer contents into a Vec. Order is oldest to
/// newest. Used by the Rust panic hook to embed recent events into
/// `crash.log`. Failure to acquire the lock returns an empty Vec (we never
/// stall the panic path on a logging mutex).
pub fn snapshot_events() -> Vec<String> {
    EVENTS_RING
        .try_lock()
        .map(|g| g.iter().cloned().collect())
        .unwrap_or_default()
}

#[cfg(windows)]
mod imp {
    use std::ffi::OsStr;
    use std::fs;
    use std::os::windows::ffi::OsStrExt;
    use std::path::{Path, PathBuf};
    use std::ptr;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::OnceLock;

    use windows_sys::Win32::Foundation::{
        CloseHandle, FALSE, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateFileW, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
    };
    use windows_sys::Win32::System::Diagnostics::Debug::{
        MiniDumpWithDataSegs, MiniDumpWithIndirectlyReferencedMemory, MiniDumpWithThreadInfo,
        MiniDumpWithUnloadedModules, MiniDumpWriteDump, SetUnhandledExceptionFilter,
        EXCEPTION_POINTERS, LPTOP_LEVEL_EXCEPTION_FILTER, MINIDUMP_EXCEPTION_INFORMATION,
    };

    // Declared by hand to avoid pulling another windows-sys feature flag for
    // a single one-off call. WriteFile's Win32 signature is stable, and we
    // already use this pattern elsewhere in the desktop crate (e.g. the
    // crash dialog calls MessageBoxW the same way).
    extern "system" {
        fn WriteFile(
            file: HANDLE,
            buffer: *const u8,
            bytes_to_write: u32,
            bytes_written: *mut u32,
            overlapped: *mut core::ffi::c_void,
        ) -> i32;
    }
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, GetCurrentProcessId, GetCurrentThreadId,
    };

    /// `EXCEPTION_CONTINUE_SEARCH` — value the OS wants from our filter so
    /// it can still hand control to WER / debugger / process termination
    /// after our dump finishes. Defined here to avoid needing the
    /// `Win32_System_Kernel` feature.
    const EXCEPTION_CONTINUE_SEARCH: i32 = 0;

    /// Pre-built wide-char prefix `<dump_dir>\openakita-`. We append the
    /// PID + tick suffix in the (signal-safe) handler.
    static DUMP_PATH_PREFIX_W: OnceLock<Vec<u16>> = OnceLock::new();
    const MAX_DUMP_PATH_W: usize = 1024;

    /// Re-entry guard. If the handler itself fails and the OS dispatches
    /// the same exception path twice (rare but possible during stack
    /// unwind), we skip the second pass instead of risking a double-dump.
    static IN_CRASH: AtomicBool = AtomicBool::new(false);

    /// Stash the previous top-level filter so we can chain. Tauri/wry
    /// install their own filter on some builds and we want to play nice.
    static PREV_FILTER: OnceLock<LPTOP_LEVEL_EXCEPTION_FILTER> = OnceLock::new();

    pub fn install(dump_dir: &Path) {
        if let Err(e) = fs::create_dir_all(dump_dir) {
            eprintln!("[crash-handler] mkdir {} failed: {e}", dump_dir.display());
            return;
        }

        // Pre-build the wide-char prefix so the crash callback doesn't
        // need to touch formatting APIs. Layout: "<dump_dir>\openakita-".
        let prefix: Vec<u16> = OsStr::new(&format!("{}\\openakita-", dump_dir.display()))
            .encode_wide()
            .collect();
        // The handler uses a fixed stack buffer. If the prefix alone is
        // too long, installing a handler that cannot write a path would
        // only create false confidence.
        if prefix.len() + 64 >= MAX_DUMP_PATH_W {
            eprintln!(
                "[crash-handler] dump path prefix too long: {}",
                dump_dir.display()
            );
            return;
        }
        if DUMP_PATH_PREFIX_W.set(prefix).is_err() {
            // install() already called once; nothing else to do.
            return;
        }

        prune_old_dumps(dump_dir, 5);

        unsafe {
            let prev = SetUnhandledExceptionFilter(Some(crash_filter));
            // Best-effort: if someone else already set a filter, remember
            // it so we can chain. `SetUnhandledExceptionFilter` always
            // returns the prior pointer (or NULL).
            let _ = PREV_FILTER.set(prev);
        }
    }

    /// Best-effort retention: keep newest `keep` dumps, delete the rest.
    fn prune_old_dumps(dir: &Path, keep: usize) {
        let Ok(rd) = fs::read_dir(dir) else { return };
        let mut entries: Vec<(PathBuf, std::time::SystemTime)> = rd
            .flatten()
            .filter_map(|e| {
                let p = e.path();
                let ext_ok = p
                    .extension()
                    .and_then(|s| s.to_str())
                    .map(|s| s.eq_ignore_ascii_case("dmp"))
                    .unwrap_or(false);
                if !ext_ok {
                    return None;
                }
                let m = fs::metadata(&p).ok()?.modified().ok()?;
                Some((p, m))
            })
            .collect();
        entries.sort_by(|a, b| b.1.cmp(&a.1));
        for (p, _) in entries.into_iter().skip(keep) {
            let _ = fs::remove_file(&p);
        }
    }

    /// Append `n` (u64, base 10) to `buf` as UTF-16 code units. Allocation-
    /// free, safe to call from the crash handler.
    fn push_u64_w(buf: &mut [u16], pos: &mut usize, n: u64) -> bool {
        if n == 0 {
            return push_w(buf, pos, b'0' as u16);
        }
        let mut tmp = [0u16; 20]; // u64::MAX has 20 digits
        let mut i = 0;
        let mut n = n;
        while n > 0 {
            tmp[i] = b'0' as u16 + (n % 10) as u16;
            n /= 10;
            i += 1;
        }
        while i > 0 {
            i -= 1;
            if !push_w(buf, pos, tmp[i]) {
                return false;
            }
        }
        true
    }

    fn push_w(buf: &mut [u16], pos: &mut usize, ch: u16) -> bool {
        if *pos >= buf.len() {
            return false;
        }
        buf[*pos] = ch;
        *pos += 1;
        true
    }

    unsafe extern "system" fn crash_filter(info: *const EXCEPTION_POINTERS) -> i32 {
        // Re-entry guard: if we crashed *inside* the handler, just chain.
        if IN_CRASH.swap(true, Ordering::SeqCst) {
            return chain(info);
        }

        let prefix_w = match DUMP_PATH_PREFIX_W.get() {
            Some(p) => p,
            None => return chain(info),
        };

        // Build "<dir>\openakita-<pid>-<tick>.dmp\0" in a fixed stack
        // buffer. Do not allocate here: 0xc0000374 means the process heap
        // is already corrupt, so even a small Vec clone can fail before
        // MiniDumpWriteDump gets a chance to run.
        if prefix_w.len() >= MAX_DUMP_PATH_W {
            return chain(info);
        }
        let mut path_w = [0u16; MAX_DUMP_PATH_W];
        let mut path_len = prefix_w.len();
        path_w[..path_len].copy_from_slice(prefix_w);
        if !push_u64_w(&mut path_w, &mut path_len, GetCurrentProcessId() as u64)
            || !push_w(&mut path_w, &mut path_len, b'-' as u16)
            || !push_u64_w(&mut path_w, &mut path_len, GetTickCount64())
        {
            return chain(info);
        }
        for ch in ['.', 'd', 'm', 'p'] {
            if !push_w(&mut path_w, &mut path_len, ch as u16) {
                return chain(info);
            }
        }
        if !push_w(&mut path_w, &mut path_len, 0) {
            return chain(info);
        }

        let file: HANDLE = CreateFileW(
            path_w.as_ptr(),
            GENERIC_WRITE,
            0,
            ptr::null(),
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            ptr::null_mut::<core::ffi::c_void>() as HANDLE,
        );
        if file.is_null() || file == INVALID_HANDLE_VALUE {
            return chain(info);
        }

        let mut exc_info = MINIDUMP_EXCEPTION_INFORMATION {
            ThreadId: GetCurrentThreadId(),
            ExceptionPointers: info as *mut EXCEPTION_POINTERS,
            ClientPointers: FALSE,
        };

        // MINIDUMP_TYPE in windows-sys 0.59 is an i32 newtype; combining
        // the With* constants stays i32 so we pass through unchanged.
        //
        // WithIndirectlyReferencedMemory is the single most useful flag
        // beyond the defaults: it walks each thread's stack and registers,
        // copies anything they point at into the dump, and lets WinDbg
        // resolve heap-allocated structs (Strings, Vecs, Tauri handles)
        // sitting on the stack at crash time. Roughly +50% dump size for
        // an order of magnitude more readable post-mortems.
        let dump_type = MiniDumpWithDataSegs
            | MiniDumpWithThreadInfo
            | MiniDumpWithUnloadedModules
            | MiniDumpWithIndirectlyReferencedMemory;

        let _ok = MiniDumpWriteDump(
            GetCurrentProcess(),
            GetCurrentProcessId(),
            file,
            dump_type,
            if info.is_null() {
                ptr::null()
            } else {
                &exc_info as *const _
            },
            ptr::null(),
            ptr::null(),
        );

        let _ = CloseHandle(file);

        // Best-effort: write a sibling `<pid>-<tick>.events.txt` containing
        // the last ~64 entries of the events ring buffer. This is the only
        // diagnostic axis the Rust panic hook gets but the SEH path
        // historically did not — without it, every minidump arrives with
        // an unsymbolized address and zero context about what user action
        // happened just before the crash.
        //
        // Layout reuses the dump path buffer up to the trailing ".dmp\0"
        // and overwrites that suffix with ".events.txt\0".
        write_sibling_events(&mut path_w, path_len);

        // We deliberately do NOT call IN_CRASH.store(false, …): if the
        // OS unwinds and tries again, we want to fall through.
        let _ = &mut exc_info; // keep alive until CloseHandle returns
        chain(info)
    }

    /// Replace the trailing ".dmp\0" suffix on a fully-built dump path with
    /// ".events.txt\0", open the file, and dump the events ring buffer.
    /// Pure best-effort: any IO failure is swallowed because by definition
    /// we are inside an unhandled exception filter and have no fallback
    /// reporting channel.
    unsafe fn write_sibling_events(path_w: &mut [u16; MAX_DUMP_PATH_W], dmp_path_len: usize) {
        // dmp_path_len includes the trailing NUL after ".dmp". Strip
        // ".dmp\0" (5 wide chars) and append ".events.txt\0" (12 wide
        // chars). Length sanity-checked to avoid running off the buffer.
        const SUFFIX: [u16; 12] = [
            b'.' as u16, b'e' as u16, b'v' as u16, b'e' as u16, b'n' as u16, b't' as u16,
            b's' as u16, b'.' as u16, b't' as u16, b'x' as u16, b't' as u16, 0,
        ];
        if dmp_path_len < 5 {
            return;
        }
        let mut len = dmp_path_len - 5;
        if len + SUFFIX.len() > path_w.len() {
            return;
        }
        path_w[len..len + SUFFIX.len()].copy_from_slice(&SUFFIX);
        len += SUFFIX.len();

        let file: HANDLE = CreateFileW(
            path_w.as_ptr(),
            GENERIC_WRITE,
            0,
            ptr::null(),
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            ptr::null_mut::<core::ffi::c_void>() as HANDLE,
        );
        if file.is_null() || file == INVALID_HANDLE_VALUE {
            let _ = len; // silence unused
            return;
        }

        // try_lock the events ring. If it fails (another thread holds the
        // lock or the heap is corrupt), write a marker so the dump still
        // contains evidence the crash filter ran.
        match super::EVENTS_RING.try_lock() {
            Ok(g) => {
                for entry in g.iter() {
                    let bytes = entry.as_bytes();
                    let mut written: u32 = 0;
                    let _ = WriteFile(
                        file,
                        bytes.as_ptr(),
                        bytes.len() as u32,
                        &mut written,
                        ptr::null_mut(),
                    );
                    let nl = b"\n";
                    let mut nlw: u32 = 0;
                    let _ = WriteFile(file, nl.as_ptr(), 1, &mut nlw, ptr::null_mut());
                }
            }
            Err(_) => {
                let marker = b"[crash-handler] events ring lock unavailable\n";
                let mut written: u32 = 0;
                let _ = WriteFile(
                    file,
                    marker.as_ptr(),
                    marker.len() as u32,
                    &mut written,
                    ptr::null_mut(),
                );
            }
        }

        let _ = CloseHandle(file);
    }

    unsafe fn chain(info: *const EXCEPTION_POINTERS) -> i32 {
        if let Some(Some(prev)) = PREV_FILTER.get().copied() {
            prev(info as *mut _)
        } else {
            EXCEPTION_CONTINUE_SEARCH
        }
    }
}

#[cfg(not(windows))]
mod imp {
    use std::path::Path;
    pub fn install(_dump_dir: &Path) {}
}

/// Install the native crash handler. Idempotent: calling twice is a no-op.
/// Safe to call before Tauri starts.
pub fn install(dump_dir: PathBuf) {
    imp::install(&dump_dir);
}
