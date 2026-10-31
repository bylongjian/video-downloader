<script setup>
defineProps({name:{type:String,required:true}})
const paths = {
    'arrow-right':'M5 12h14m-6-6 6 6-6 6', 'arrow-left':'M19 12H5m6-6-6 6 6 6', 'arrow-up-right':'M6 18 18 6M6 6h12v12',
    'chevron-down':'m6 9 6 6 6-6', 'chevron-up':'m6 15 6-6 6 6', 'chevron-left':'m15 6-6 6 6 6', 'chevron-right':'m9 6 6 6-6 6',
    'x':'m6 6 12 12M6 18 18 6', 'plus':'M12 5v14M5 12h14', 'minus':'M5 12h14', 'menu':'M4 6h16M4 12h16M4 18h16',
    'link':'m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0',
    'clipboard':'M9 4H6a2 2 0 0 0-2 2v14h16V6a2 2 0 0 0-2-2h-3M9 2h6v4H9z',
    'sparkles':'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4M18 4h4',
    'check-circle':'M9 12l2 2 5-5M21 12a9 9 0 1 1-5-8', 'check':'m5 12 4 4L19 6',
    'globe':'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3a17 17 0 0 1 0 18 17 17 0 0 1 0-18Z',
    'zap':'m13 2-9 12h7l-1 8 10-12h-7l1-8Z', 'monitor':'M3 4h18v13H3zM12 17v4M8 21h8',
    'smartphone':'M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1ZM11 18h2',
    'file-text':'M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h8',
    'copy':'M9 9h12v12H9zM5 15H3V3h12v2', 'download':'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
    'coffee':'M3 8h13v10a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V8ZM16 9h2a3 3 0 1 1 0 6h-2M6 2v3M10 2v3M14 2v3',
    'crown':'m3 6 4 5 5-8 5 8 4-5-2 13H5L3 6ZM6 22h12',
    'sliders':'M4 6h9M17 6h3M4 12h3m4 0h9M4 18h11m4 0h1M13 3v6M7 9v6m8 0v6',
    'shield-check':'M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6l-9-4Zm-4 10 3 3 5-6',
    'info':'M12 8h.01M12 12v5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    'captions':'M3 5h18v14H3zM6 10h5M6 14h3m4-4h5m-5 4h5',
    'git-branch':'M6 3v12m0-8h9a3 3 0 0 0 3-3M6 12h9a3 3 0 0 1 3 3M4 17h4v4H4zM16 1h4v4h-4zM16 17h4v4h-4z',
    'message-circle':'M21 11.5a9 9 0 0 1-9 9 9.5 9.5 0 0 1-4-.9L3 21l1.4-4.4A9 9 0 1 1 21 11.5ZM8 12h.01M12 12h.01M16 12h.01',
    'rotate-cw':'M20 7v5h-5M20 12a8 8 0 1 0-2 5', 'expand':'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
    'eye':'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    'eye-off':'m3 3 18 18M10 5a12 12 0 0 1 12 7 17 17 0 0 1-4 4M6 6a18 18 0 0 0-4 6s4 7 10 7a12 12 0 0 0 5-1M10 10a3 3 0 0 0 4 4',
    'log-out':'M9 3H3v18h6m5-14 5 5-5 5M7 12h12', 'send':'m22 2-7 20-4-9-9-4 20-7ZM11 13 22 2',
    'alert-circle':'M12 7v6m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    'search':'m21 21-6-6M17 9a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z', 'play':'m8 4 12 8-12 8V4Z',
    'lock':'M5 10h14v12H5zM8 10V6a4 4 0 0 1 8 0v4', 'move':'M12 2v20M2 12h20m-13-7 3-3 3 3m4 4 3 3-3 3M9 19l3 3 3-3M5 9l-3 3 3 3'
  }
</script>
<template><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paths[name] || paths.info" /></svg></template>
