<template>
<section class="section showcase-section" id="features">
      <div class="container">
        <div class="section-heading"><div><p class="kicker">MORE THAN A DOWNLOAD</p><h2>不止保存视频，<br class="mobile-only">更把知识带走。</h2></div><p>把长视频变成随手可用的学习笔记。<br>少一点来回拖进度，多一点真正的收获。</p></div>
        <div class="showcase-grid">
          <article class="feature-showcase"><div class="feature-label"><Icon name="sparkles" class="icon-tile blue-tile" /><span>你的 AI 视频学习助手</span><span class="tag">核心功能</span></div><h3>20 分钟的内容，<br>用 2 分钟抓住重点。</h3><p>摘要、字幕、思维导图、自由问答，<br>用适合自己的方式理解每一个视频。</p><button class="text-button" @click="$emit('example')">亲自体验一下<Icon name="arrow-up-right"  /></button><div class="mini-summary"><div class="mini-summary-heading"><Icon name="file-text"  />视频核心要点<span class="mini-done">已整理</span></div><div class="mini-point"><span>01</span><div><b>先理解，再收藏</b><p>从视频中提取观点，让知识更有条理。</p></div></div><div class="mini-point"><span>02</span><div><b>把灵感连接起来</b><p>用可交互导图看清内容之间的联系。</p></div></div><div class="mini-summary-bottom"><span class="status-dot"></span>基于视频字幕生成 <span>示例预览</span></div></div></article>
          <div class="small-feature-grid"><article class="small-feature"><Icon name="globe" class="icon-tile blue-tile" /><h3>喜欢的，都能找到</h3><p>基于 yt-dlp 解析多平台链接，<br>可用性受平台权限与网络影响。</p><span class="feature-number">1800<span>+</span></span></article><article class="small-feature"><Icon name="zap" class="icon-tile green-tile" /><h3>少等待，快一步</h3><p>智能解析链接，自动匹配<br>合适的下载方式。</p><div class="speed-visual"><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div></article><article class="small-feature"><Icon name="monitor" class="icon-tile amber-tile" /><h3>清晰度，你来选</h3><p>按源视频提供的可用格式，<br>选择适合自己的画质。</p><div class="quality-visual"><span>720p</span><b>1080p</b><span>4K</span></div></article><article class="small-feature"><Icon name="smartphone" class="icon-tile purple-tile" /><h3>口袋里的下载器</h3><p>打开手机浏览器就能用，<br>不必再安装一个 App。</p><div class="devices-visual"><Icon name="monitor"  /><Icon name="smartphone"  /><span class="status-dot"></span><span>随时就绪</span></div></article></div>
        </div>
      </div>
    </section>
</template>
