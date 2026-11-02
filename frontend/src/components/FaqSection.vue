<script setup>
import {faqItems,contentUpdated} from '../content/product.js'
</script>

<template>
  <section class="section faq-section" id="faq"><div class="container faq-grid">
    <div><p class="kicker">A FEW THINGS TO KNOW</p><h2>视频下载与 AI 总结常见问题</h2><p>先了解使用条件，再开始处理视频。</p><p class="muted tiny">更新于 <time :datetime="contentUpdated">{{contentUpdated}}</time></p><a class="text-button" href="/guide/">阅读完整使用指南<Icon name="arrow-up-right" /></a></div>
    <div class="faq-items"><details v-for="item in faqItems" :key="item.question"><summary>{{item.question}}<Icon name="plus" /></summary><p>{{item.answer}}</p></details></div>
  </div></section>
</template>