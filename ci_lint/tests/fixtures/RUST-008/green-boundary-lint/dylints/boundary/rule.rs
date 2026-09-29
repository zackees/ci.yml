// Placeholder Dylint rule confining platform-specific `cfg` to a reviewed
// module (soldr#2762 pattern). Its mere presence under dylints/ with
// "boundary" in the path is RUST-008's static proof that platform cfg is
// confined, exempting the repo from requiring check-only coverage for
// every declared target from the quick gate job.
