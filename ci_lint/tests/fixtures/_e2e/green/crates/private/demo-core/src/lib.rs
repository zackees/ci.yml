pub fn greeting() -> &'static str {
    "hello"
}

#[test]
fn test_greeting() {
    assert_eq!(greeting(), "hello");
}
