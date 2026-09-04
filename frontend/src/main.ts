import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './styles/main.css'
import './styles/redesign.css'
import './styles/finishing.css'
import './styles/main-area.css'
import './styles/monitoring.css'
import './styles/accounts.css'
import './styles/tweets.css'
import './styles/logs.css'
import './styles/x-authorization.css'
import './styles/ai-data-source.css'
import './styles/ai-writing.css'
import './styles/qq-notifications.css'
import './styles/qq-tasks.css'
import './styles/settings.css'
import './styles/login.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
