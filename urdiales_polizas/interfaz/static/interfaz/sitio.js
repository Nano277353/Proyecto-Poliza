"use strict";

// Los formularios se envían normalmente a Django; JavaScript sólo facilita la captura.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-add-formset]");
  if (!button) return;
  const prefix = button.dataset.addFormset;
  const group = Array.from(document.querySelectorAll("[data-formset-prefix]"))
    .find((element) => element.dataset.formsetPrefix === prefix);
  const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
  const template = group?.querySelector("template[data-empty-form]");
  const rows = group?.querySelector("[data-formset-rows]");
  if (!total || !template || !rows) return;
  event.preventDefault();
  const index = Number.parseInt(total.value, 10);
  if (!Number.isInteger(index) || index < 0 || index >= 500) return;
  rows.insertAdjacentHTML("beforeend", template.innerHTML.replaceAll("__prefix__", index));
  total.value = String(index + 1);
  rows.lastElementChild?.querySelector("input:not([type=hidden]),select,textarea")?.focus();
});

function mostrarCamposDeSeguro() {
  const tipo = document.querySelector('select[name="tipo"]');
  if (!tipo) return;
  const esAuto = tipo.value === "autos";
  document.querySelectorAll("[data-auto-fields]").forEach((element) => {
    element.hidden = !esAuto;
  });
  document.querySelectorAll("[data-danios-fields]").forEach((element) => {
    element.hidden = esAuto;
  });
}

document.addEventListener("change", (event) => {
  if (event.target.matches('select[name="tipo"]')) mostrarCamposDeSeguro();
});
mostrarCamposDeSeguro();
