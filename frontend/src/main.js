import { createApp } from 'vue'
import App from './App.vue'
import Icon from './components/Icon.vue'
import './style.css'

createApp(App).component('Icon', Icon).mount('#app')
