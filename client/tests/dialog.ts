// jsdom has no native dialog lifecycle. Browser verification covers modality/focus trapping.
export function installDialogStub() {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '')
    this.querySelector<HTMLInputElement>('input')?.focus()
  }
  HTMLDialogElement.prototype.close = function () {
    if (!this.open) return
    this.removeAttribute('open')
    this.dispatchEvent(new Event('close'))
  }
}
