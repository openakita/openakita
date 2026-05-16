//! Native crash handler that captures minidumps for SEH exceptions
//! (0xc0000005 access violation / 0xc0000374 heap corruption /
//! 0xc000001d illegal instruction / etc.) which `std::panic::set_hook`
//! cannot see.
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

use std::path::PathBuf;

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
        MiniDumpWithDataSegs, MiniDumpWithThreadInfo, MiniDumpWithUnloadedModules,
        MiniDumpWriteDump, SetUnhandledExceptionFilter, EXCEPTION_POINTERS,
        LPTOP_LEVEL_EXCEPTION_FILTER, MINIDUMP_EXCEPTION_INFORMATION,
    };
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
        let dump_type = MiniDumpWithDataSegs
            | MiniDumpWithThreadInfo
            | MiniDumpWithUnloadedModules;

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
        // We deliberately do NOT call IN_CRASH.store(false, …): if the
        // OS unwinds and tries again, we want to fall through.
        let _ = &mut exc_info; // keep alive until CloseHandle returns
        chain(info)
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
