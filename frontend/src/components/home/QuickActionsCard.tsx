'use client'

import Link from 'next/link'
import { BookPlus, FileUp, MessageCircleQuestion, Mic } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * Shortcuts to the existing creation flows (CreateDialogsProvider, already
 * mounted app-wide - see lib/hooks/use-create-dialogs.tsx) - no new dialogs,
 * just entry points. "Ask a question" has no cross-notebook dialog of its
 * own, so it links to /search (Ask across sources) instead.
 */
export function QuickActionsCard() {
  const { t } = useTranslation()
  const { openNotebookDialog, openSourceDialog, openPodcastDialog } = useCreateDialogs()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t('home.quickActions')}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Button variant="outline" className="h-auto flex-col gap-2 py-4" onClick={openNotebookDialog}>
          <BookPlus className="h-4 w-4 text-teal" />
          <span className="text-xs">{t('home.newNotebook')}</span>
        </Button>
        <Button variant="outline" className="h-auto flex-col gap-2 py-4" onClick={openSourceDialog}>
          <FileUp className="h-4 w-4 text-sage" />
          <span className="text-xs">{t('home.addSource')}</span>
        </Button>
        <Button asChild variant="outline" className="h-auto flex-col gap-2 py-4">
          <Link href="/search">
            <MessageCircleQuestion className="h-4 w-4 text-teal" />
            <span className="text-xs">{t('home.askAQuestion')}</span>
          </Link>
        </Button>
        <Button variant="outline" className="h-auto flex-col gap-2 py-4" onClick={openPodcastDialog}>
          <Mic className="h-4 w-4 text-mauve" />
          <span className="text-xs">{t('home.generatePodcast')}</span>
        </Button>
      </CardContent>
    </Card>
  )
}
