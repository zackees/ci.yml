pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[test]
fn it_adds() {
    assert_eq!(2, add(1, 1));
}
