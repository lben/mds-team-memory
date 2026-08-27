import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/HomeView.vue') },
    { path: '/scratchpad', component: () => import('./views/ScratchpadView.vue') },
    { path: '/documents/:id?', component: () => import('./views/DocumentsView.vue') },
    { path: '/leaderboard', component: () => import('./views/LeaderboardView.vue') },
    { path: '/admin/expertise', component: () => import('./views/AdminExpertiseView.vue') },
    // Old routes from before the single-window redesign.
    { path: '/impact', redirect: '/leaderboard' },
    { path: '/capture', redirect: '/' },
    { path: '/search', redirect: (to) => ({ path: '/', query: to.query }) },
    { path: '/context', redirect: '/' },
    { path: '/questions/:id?', redirect: (to) => ({ path: '/', query: { question: to.params.id || undefined } }) },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
