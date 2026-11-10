def find_user(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    cursor.execute("SELECT * FROM users WHERE id = :id", {"id": user_id})
