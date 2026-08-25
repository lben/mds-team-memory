import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/capture' },
    { path: '/capture', component: () => import('./views/CaptureView.vue') },
    { path: '/search', component: () => import('./views/SearchView.vue') },
    { path: '/questions/:id?', component: () => import('./views/QuestionsView.vue') },
    { path: '/scratchpad', component: () => import('./views/ScratchpadView.vue') },
    { path: '/documents/:id?', component: () => import('./views/DocumentsView.vue') },
    { path: '/context', component: () => import('./views/ContextView.vue') },
    { path: '/impact', component: () => import('./views/ImpactView.vue') },
    { path: '/admin/expertise', component: () => import('./views/AdminExpertiseView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/capture' },
  ],
})
