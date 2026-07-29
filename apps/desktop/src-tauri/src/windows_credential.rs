use std::fmt;
use std::ptr::null_mut;

use windows_sys::Win32::Foundation::{ERROR_NOT_FOUND, GetLastError};
use windows_sys::Win32::Security::Credentials::{
    CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC, CREDENTIALW, CredDeleteW, CredFree, CredReadW,
    CredWriteW,
};

const ROOT_KEY_BYTES: usize = 32;

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        value.push(DIGITS[(byte >> 4) as usize] as char);
        value.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    value
}

#[derive(PartialEq)]
pub struct CredentialSecret([u8; ROOT_KEY_BYTES]);

impl CredentialSecret {
    pub fn expose_for_bootstrap(&self) -> String {
        hex(&self.0)
    }

    pub(crate) fn bytes(&self) -> [u8; ROOT_KEY_BYTES] {
        self.0
    }
}

impl fmt::Debug for CredentialSecret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CredentialSecret([redacted])")
    }
}

impl Drop for CredentialSecret {
    fn drop(&mut self) {
        self.0.fill(0);
    }
}

pub struct WindowsCredentialStore {
    target: String,
}

impl WindowsCredentialStore {
    pub fn new(target: String) -> Self {
        Self { target }
    }

    pub fn read(&self) -> Result<Option<CredentialSecret>, &'static str> {
        let target = wide(&self.target);
        let mut raw: *mut CREDENTIALW = null_mut();
        // SAFETY: `target` is nul-terminated and `raw` is an out pointer owned by CredFree.
        if unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) } == 0 {
            // SAFETY: GetLastError immediately follows the failed Win32 call.
            return if unsafe { GetLastError() } == ERROR_NOT_FOUND {
                Ok(None)
            } else {
                Err("LOCAL_CREDENTIAL_READ_FAILED")
            };
        }
        if raw.is_null() {
            return Err("LOCAL_CREDENTIAL_READ_FAILED");
        }
        // SAFETY: CredReadW returned a valid CREDENTIALW allocation.
        let credential = unsafe { &*raw };
        let result = if credential.CredentialBlobSize as usize == ROOT_KEY_BYTES
            && !credential.CredentialBlob.is_null()
        {
            let mut key = [0_u8; ROOT_KEY_BYTES];
            // SAFETY: CredentialBlobSize was checked and remains valid until CredFree.
            key.copy_from_slice(unsafe {
                std::slice::from_raw_parts(credential.CredentialBlob, ROOT_KEY_BYTES)
            });
            let secret = CredentialSecret(key);
            key.fill(0);
            Ok(Some(secret))
        } else {
            Err("LOCAL_CREDENTIAL_INVALID")
        };
        // SAFETY: `raw` is the exact allocation returned by CredReadW and is freed once.
        unsafe { CredFree(raw.cast()) };
        result
    }

    pub fn load_or_create(
        &self,
        existing_ciphertext: bool,
    ) -> Result<CredentialSecret, &'static str> {
        if let Some(secret) = self.read()? {
            return Ok(secret);
        }
        if existing_ciphertext {
            return Err("LOCAL_KEY_UNAVAILABLE");
        }
        let mut key = [0_u8; ROOT_KEY_BYTES];
        getrandom::fill(&mut key).map_err(|_| "LOCAL_RANDOM_UNAVAILABLE")?;
        let secret = CredentialSecret(key);
        key.fill(0);
        self.write(&secret)?;
        Ok(secret)
    }

    fn write(&self, secret: &CredentialSecret) -> Result<(), &'static str> {
        let mut target = wide(&self.target);
        let mut username = wide("Daon User");
        let credential = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: target.as_mut_ptr(),
            CredentialBlobSize: ROOT_KEY_BYTES as u32,
            CredentialBlob: secret.0.as_ptr().cast_mut(),
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            UserName: username.as_mut_ptr(),
            ..CREDENTIALW::default()
        };
        // SAFETY: All pointers reference live buffers for the duration of CredWriteW.
        if unsafe { CredWriteW(&credential, 0) } == 0 {
            Err("LOCAL_CREDENTIAL_WRITE_FAILED")
        } else {
            Ok(())
        }
    }

    pub fn revoke(&self) -> Result<(), &'static str> {
        let target = wide(&self.target);
        // SAFETY: `target` is a live nul-terminated UTF-16 buffer.
        if unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) } != 0 {
            return Ok(());
        }
        // SAFETY: GetLastError immediately follows the failed Win32 call.
        if unsafe { GetLastError() } == ERROR_NOT_FOUND {
            Ok(())
        } else {
            Err("LOCAL_CREDENTIAL_DELETE_FAILED")
        }
    }
}
