import { AppRegistry } from "react-native";
import App from "./src/App.tsx";
import app from "./app.json";

AppRegistry.registerComponent(app.name, () => App);
