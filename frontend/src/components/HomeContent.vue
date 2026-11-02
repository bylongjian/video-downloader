<script setup>
import {ref} from 'vue'
import HeroSection from './HeroSection.vue'
import FeatureSection from './FeatureSection.vue'
import HowToSection from './HowToSection.vue'
import PricingSection from './PricingSection.vue'
import FaqSection from './FaqSection.vue'
import BottomCta from './BottomCta.vue'
defineEmits(['parse','notice','example','start','upgrade'])
const hero=ref(null)
defineExpose({focus:()=>hero.value.focus(),example:name=>hero.value.example(name)})
</script>

<template>
  <HeroSection ref="hero" @parse="$emit('parse',$event)" @notice="$emit('notice',$event)" />
  <FeatureSection @example="$emit('example')" />
  <HowToSection />
  <PricingSection @start="$emit('start')" @upgrade="$emit('upgrade')" />
  <FaqSection />
  <BottomCta />
</template>
