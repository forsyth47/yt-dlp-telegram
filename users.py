import json
import os

DATA_FILE = "data/userdata.json"

class UserManager:
    def __init__(self):
        self.data = {"users": []}
        self.load_data()

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"Error loading user data: {e}")
                self.data = {"users": []}
        else:
            self.save_data()

    def save_data(self):
        try:
            if not os.path.exists(os.path.dirname(DATA_FILE)):
                os.makedirs(os.path.dirname(DATA_FILE))
            with open(DATA_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving user data: {e}")

    def get_user(self, user_id):
        for user in self.data["users"]:
            if user["id"] == user_id:
                return user
        return None

    def add_user(self, user_id):
        if not self.get_user(user_id):
            new_user = {"id": user_id, "quality": "ask"}
            self.data["users"].append(new_user)
            self.save_data()
            return new_user
        return self.get_user(user_id)

    def set_quality(self, user_id, quality):
        user = self.get_user(user_id)
        if user:
            user["quality"] = quality
            self.save_data()
        else:
            self.add_user(user_id)
            self.set_quality(user_id, quality)

    def get_quality(self, user_id):
        user = self.get_user(user_id)
        if user:
            return user.get("quality", "ask")
        else:
            self.add_user(user_id)
            return "720"
        