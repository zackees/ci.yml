//! demo-core: a private, feature-free crate (round-2A brief, defect 2).
//!
//! This doc comment merely *mentions* `cfg(feature = "json")` the way
//! `crates/private/template-json/src/lib.rs` in the real template repo
//! does, to explain that the *amalgam* crate wires this crate in behind a
//! feature -- `demo-core` itself has no `[features]` and no real
//! `cfg(feature = ...)` anywhere in its own code, so RUST-011 must not
//! fire just because the doc comment's text contains that substring.

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
