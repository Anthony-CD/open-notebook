'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AddSourceDialog } from '@/components/sources/AddSourceDialog'
import { useCreateNotebook } from '@/lib/hooks/use-notebooks'
import { useOnboardingStore } from '@/lib/stores/onboarding-store'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'

interface OnboardingFlowProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type Step = 0 | 1 | 2 | 3

/**
 * Skippable first-run flow: create a notebook, add a source, then open the
 * notebook to ask about it - see lib/stores/onboarding-store.ts for the
 * persisted completed/dismissed flags this reads and writes.
 *
 * Composes existing creation flows instead of reimplementing them: step 1
 * uses the same useCreateNotebook() mutation CreateNotebookDialog uses
 * (inline here, so the created notebook's id is available to scope step 2),
 * and step 2 is the real AddSourceDialog with defaultNotebookId set - this
 * component is hidden while that dialog is open so there's only ever one
 * visible modal, then resumes at step 3 once it closes. Step 3 deliberately
 * doesn't reimplement the ask flow - it hands off to the notebook page's
 * own chat column, which is the real one.
 *
 * Going back after a notebook has been created is intentionally not
 * offered (re-submitting step 1 would create a second notebook) - Back
 * only exists on the welcome step.
 */
export function OnboardingFlow({ open, onOpenChange }: OnboardingFlowProps) {
  const { t } = useTranslation()
  const router = useRouter()
  const { toast } = useToast()
  const complete = useOnboardingStore((state) => state.complete)
  const dismiss = useOnboardingStore((state) => state.dismiss)
  const createNotebook = useCreateNotebook()

  const [step, setStep] = useState<Step>(0)
  const [notebookName, setNotebookName] = useState('')
  const [notebookId, setNotebookId] = useState<string | null>(null)
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)

  const resetLocalState = () => {
    setStep(0)
    setNotebookName('')
    setNotebookId(null)
    setSourceDialogOpen(false)
  }

  const handleSkipAll = () => {
    dismiss()
    onOpenChange(false)
    resetLocalState()
  }

  const handleFinish = (navigateToNotebook: boolean) => {
    complete()
    onOpenChange(false)
    toast({ title: t('onboarding.completedToast') })
    if (navigateToNotebook && notebookId) {
      router.push(`/notebooks/${encodeURIComponent(notebookId)}`)
    }
    resetLocalState()
  }

  const handleCreateNotebook = async () => {
    if (!notebookName.trim()) return
    const notebook = await createNotebook.mutateAsync({ name: notebookName.trim() })
    setNotebookId(notebook.id)
    setStep(2)
  }

  const handleSourceDialogChange = (sourceOpen: boolean) => {
    setSourceDialogOpen(sourceOpen)
    if (!sourceOpen) {
      setStep(3)
    }
  }

  return (
    <>
      {/* Hidden (not unmounted) while AddSourceDialog is open, so only one
          modal is visible at a time but onboarding state survives. */}
      <Dialog
        open={open && !sourceDialogOpen}
        onOpenChange={(next) => {
          if (!next) handleSkipAll()
        }}
      >
        <DialogContent className="sm:max-w-[480px]">
          {step === 0 && (
            <>
              <DialogHeader>
                <DialogTitle>{t('onboarding.welcomeTitle')}</DialogTitle>
                <DialogDescription>{t('onboarding.welcomeDesc')}</DialogDescription>
              </DialogHeader>
              <DialogFooter className="flex sm:justify-between">
                <Button variant="ghost" onClick={handleSkipAll}>
                  {t('onboarding.skipAll')}
                </Button>
                <Button onClick={() => setStep(1)}>{t('onboarding.start')}</Button>
              </DialogFooter>
            </>
          )}

          {step === 1 && (
            <>
              <DialogHeader>
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t('onboarding.stepLabel', { current: 1, total: 3 })}
                </p>
                <DialogTitle>{t('onboarding.step1Title')}</DialogTitle>
                <DialogDescription>{t('onboarding.step1Desc')}</DialogDescription>
              </DialogHeader>
              <div className="space-y-2 py-2">
                <Label htmlFor="onboarding-notebook-name" className="sr-only">
                  {t('onboarding.notebookNamePlaceholder')}
                </Label>
                <Input
                  id="onboarding-notebook-name"
                  autoFocus
                  value={notebookName}
                  onChange={(e) => setNotebookName(e.target.value)}
                  placeholder={t('onboarding.notebookNamePlaceholder')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && notebookName.trim() && !createNotebook.isPending) {
                      handleCreateNotebook()
                    }
                  }}
                />
              </div>
              <DialogFooter className="flex sm:justify-between">
                <div className="flex gap-2">
                  <Button variant="ghost" onClick={() => setStep(0)}>
                    {t('onboarding.back')}
                  </Button>
                  <Button variant="ghost" onClick={handleSkipAll}>
                    {t('onboarding.skipStep')}
                  </Button>
                </div>
                <Button
                  onClick={handleCreateNotebook}
                  disabled={!notebookName.trim() || createNotebook.isPending}
                >
                  {t('onboarding.next')}
                </Button>
              </DialogFooter>
            </>
          )}

          {step === 2 && (
            <>
              <DialogHeader>
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t('onboarding.stepLabel', { current: 2, total: 3 })}
                </p>
                <DialogTitle>{t('onboarding.step2Title')}</DialogTitle>
                <DialogDescription>{t('onboarding.step2Desc')}</DialogDescription>
              </DialogHeader>
              <DialogFooter className="flex sm:justify-between">
                <Button variant="ghost" onClick={() => setStep(3)}>
                  {t('onboarding.skipStep')}
                </Button>
                <Button onClick={() => setSourceDialogOpen(true)}>{t('home.addSource')}</Button>
              </DialogFooter>
            </>
          )}

          {step === 3 && (
            <>
              <DialogHeader>
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t('onboarding.stepLabel', { current: 3, total: 3 })}
                </p>
                <DialogTitle>{t('onboarding.step3Title')}</DialogTitle>
                <DialogDescription>{t('onboarding.step3Desc')}</DialogDescription>
              </DialogHeader>
              <DialogFooter className="flex sm:justify-between">
                <Button variant="ghost" onClick={() => handleFinish(false)}>
                  {t('onboarding.finish')}
                </Button>
                <Button onClick={() => handleFinish(true)}>{t('onboarding.goToNotebook')}</Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <AddSourceDialog
        open={sourceDialogOpen}
        onOpenChange={handleSourceDialogChange}
        defaultNotebookId={notebookId ?? undefined}
      />
    </>
  )
}
