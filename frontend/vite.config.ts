import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {VitePWA} from 'vite-plugin-pwa';
export default defineConfig({plugins:[react(),VitePWA({registerType:'prompt',includeAssets:['brand-logo.png'],manifest:{name:'Product Image Finder by Siddique Sayed',id:'/',short_name:'Image Finder',description:'Find and review catalogue images',theme_color:'#073b2b',background_color:'#f5f6f8',display:'standalone',start_url:'/',icons:[{src:'/icon-192.png',sizes:'192x192',type:'image/png',purpose:'any'},{src:'/icon-512.png',sizes:'512x512',type:'image/png',purpose:'any'}]},workbox:{globPatterns:['**/*.{js,css,html,png}'],navigateFallbackDenylist:[/^\/api/],cleanupOutdatedCaches:true}})],server:{proxy:{'/api':'http://127.0.0.1:8000'}}});
