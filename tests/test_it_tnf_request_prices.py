"""Offline TNF request-price regressions; stdlib-only, no browser/network imports.

The text fixtures reproduce the price blocks of DET PRES/UPR 1185/2026,
pp. 6-7. Optional live PDF verification is deliberately not a unit-test dependency.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_scraper():
    # Isolate dependency stubs from the normal agents.* module graph and other tests.
    package = types.ModuleType('_tnf_price_test')
    package.__path__ = []
    base = types.ModuleType('_tnf_price_test.base')
    class Base:
        def __init__(self, **kwargs):
            self.warnings = []
        def _resolve_form_type(self, item):
            return 'oral' if 'compresse' in item['dosage_form'] else 'injection'
    base.BaseScraper = Base
    request_stub = types.ModuleType('requests')
    spec = importlib.util.spec_from_file_location('_tnf_price_test.scraper', ROOT / 'agents/scrapers/it_aifa_tnf.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'_tnf_price_test': package, '_tnf_price_test.base': base, 'requests': request_stub}):
        spec.loader.exec_module(module)
    return module


tnf = load_scraper()


def block(desc, aic, exf='3.798,34', public='6.268,78'):
    return (f'Confezione\n“{desc}”\nAIC n. {aic} (in base 10)\nClasse di rimborsabilità\nH\n'
            f'Prezzo ex-factory (IVA esclusa)\n€ {exf}\nPrezzo al pubblico (IVA inclusa)\n€ {public}\n')


IV1 = block('25 mg/ml - concentrato per soluzione per infusione - uso endovenoso - flaconcino (vetro) - 4 ml 1 flaconcino', '044386023')
IV2 = block('25 mg/ml - concentrato per soluzione per infusione - uso endovenoso - flaconcino (vetro) 4 ml 2 flaconcini', '044386035', '7.596,68', '12.537,56')
SC395 = block('395 mg - soluzione iniettabile - uso sottocutaneo - flaconcino (vetro) 2,4 ml (165 mg/ml) 1 flaconcino', '044386047', '7.596,68', '12.537,56')
SC790 = block('790 mg - soluzione iniettabile - uso sottocutaneo - flaconcino (vetro) 4,8ml (165 mg/ml) 1 flaconcino', '044386050', '15.193,36', '25.075,12')


def detail(identifier, published, brand='KEYTRUDA', effective='2020-01-01'):
    return {'id': identifier, 'data': '2020-01-02', 'dataPubblicazione': published,
            'dataEfficacia': effective, 'numeroRepertorio': identifier,
            'datiFarmaco': [{'denominazione': brand, 'principiAttivi': ['PEMBROLIZUMAB'], 'aic6': '044386'}],
            'allegati': [{'id': identifier, 'principale': True}]}


def scraper_for(documents, texts):
    scraper = object.__new__(tnf.ItAifaTnfScraper)
    scraper.warnings = []
    scraper._search_determine = Mock(return_value=[{'id': key} for key in documents])
    scraper._detail = Mock(side_effect=lambda key: documents[key])
    scraper._download_allegato = Mock(side_effect=lambda key: key)
    def parse(key):
        value = texts[key]
        if isinstance(value, Exception):
            raise value
        return tnf.ItAifaTnfScraper._parse_text_pages(value)
    scraper._parse_confezioni = Mock(side_effect=parse)
    return scraper


class TnfRequestPricesTest(unittest.TestCase):
    def test_keytruda_iv_and_sc_pack_prices(self):
        rows = tnf.ItAifaTnfScraper._parse_text_pages([IV1 + IV2 + SC395.split('Prezzo al pubblico')[0],
                                                     'Prezzo al pubblico (IVA inclusa)\n€ 12.537,56\n' + SC790])
        self.assertEqual([r['ex_factory'] for r in rows], [3798.34, 7596.68, 7596.68, 15193.36])
        self.assertEqual([r['pack'] for r in rows], [1, 2, 1, 1])
        self.assertEqual([r['aic'] for r in rows], ['044386023', '044386035', '044386047', '044386050'])
        self.assertEqual(rows[2]['pages'], [1, 2])
        self.assertEqual(rows[0]['pubblico'], 6268.78)
        self.assertIn('4 ml', rows[0]['desc'])  # concentration*volume, not 4 vials
        self.assertIn('395 mg', rows[2]['desc'])
        self.assertIn('790 mg', rows[3]['desc'])

    def test_full_description_and_welireg_90(self):
        desc = '40 mg compresse rivestite con film - uso orale - ' + 'dettaglio ' * 15 + '90 (3 x 30) compresse'
        row = tnf.ItAifaTnfScraper._parse_text_pages([block(desc, '050000001', '16.066,49')])[0]
        self.assertEqual(row['desc'], desc)
        self.assertGreater(len(row['desc']), 120)
        self.assertEqual(row['pack'], 90)
        self.assertEqual(row['ex_factory'], 16066.49)

    def test_newer_no_price_and_per_aic_merge(self):
        docs = {'old': detail('old', '2020-01-01'), 'new': detail('new', '2020-02-01'),
                'rectify': detail('rectify', '2020-03-01')}
        scraper = scraper_for(docs, {'old': [IV1 + IV2], 'new': [IV1.replace('3.798,34', '4.000,00')],
                                    'rectify': ['Rettifica: modifica delle indicazioni terapeutiche.']})
        rows = asyncio.run(scraper.search('KEYTRUDA'))
        self.assertEqual([r['local_price'] for r in rows], [4000.0, 7596.68])
        self.assertEqual([r['extra']['determina'] for r in rows], ['new', 'old'])

    def test_no_wrong_drug_fallback_or_partial_name(self):
        scraper = scraper_for({'wrong': detail('wrong', '2020-01-01', brand='OTHERDRUG')}, {'wrong': [IV1]})
        self.assertEqual(asyncio.run(scraper.search('KEYTRUDA')), [])
        scraper._download_allegato.assert_not_called()
        self.assertFalse(tnf.ItAifaTnfScraper._matches_drug(detail('x', '2020', brand='KEYTRUDAOTHER'), 'KEYTRUDA'))
        self.assertFalse(tnf.ItAifaTnfScraper._matches_drug(detail('x', '2020'), ''))

    def test_future_effective_skipped_and_dates_separate(self):
        docs = {'future': detail('future', '2020-02-01', effective='2999-01-01'),
                'old': detail('old', '2020-01-01', effective='2020-01-03')}
        scraper = scraper_for(docs, {'future': [IV2], 'old': [IV1]})
        row = asyncio.run(scraper.search('KEYTRUDA'))[0]
        self.assertEqual(row['extra']['document_date'], '2020-01-02')
        self.assertEqual(row['extra']['publication_date'], '2020-01-01')
        self.assertEqual(row['extra']['effective_date'], '2020-01-03')
        self.assertEqual(scraper._download_allegato.call_args_list[0].args, ('old',))

    def test_provenance_and_run_source_type(self):
        scraper = scraper_for({'doc': detail('doc', '2020-01-01')}, {'doc': [IV1]})
        row = asyncio.run(scraper.run('KEYTRUDA'))[0]
        evidence = json.loads(row['raw_data'])
        self.assertEqual(row['source_type'], 'aifa_exfactory')
        self.assertEqual(evidence['prezzo_ex_factory'], 3798.34)
        self.assertEqual(evidence['prezzo_pubblico'], 6268.78)
        self.assertEqual(evidence['evidence_pages'], [1])
        self.assertIn('AIC n. 044386023', evidence['evidence_text'])
        self.assertEqual(row['source_url'], evidence['pdf_url'])

    def test_parse_failure_not_zero_results(self):
        with self.assertRaises(tnf.TnfPdfParseError):
            tnf.ItAifaTnfScraper._parse_text_pages(['', ''])
        self.assertEqual(tnf.ItAifaTnfScraper._parse_text_pages(['Rettifica senza prezzi']), [])
        scraper = scraper_for({'scan': detail('scan', '2020-01-01')}, {'scan': tnf.TnfPdfParseError('scanned')})
        with self.assertRaises(tnf.TnfPdfParseError):
            asyncio.run(scraper.search('KEYTRUDA'))

    def test_parse_failure_can_continue_with_warning(self):
        docs = {'scan': detail('scan', '2020-02-01'), 'old': detail('old', '2020-01-01')}
        scraper = scraper_for(docs, {'scan': tnf.TnfPdfParseError('scanned'), 'old': [IV1]})
        self.assertEqual(len(asyncio.run(scraper.search('KEYTRUDA'))), 1)
        self.assertEqual(scraper.warnings[0]['warning_class'], 'channel_failed')

    def test_public_only_never_used(self):
        self.assertEqual(tnf.ItAifaTnfScraper._parse_text_pages([IV1.replace('Prezzo ex-factory (IVA esclusa)\n€ 3.798,34\n', '')]), [])

    def test_wrong_aic_in_matching_multi_drug_document(self):
        doc = detail('multi', '2020-01-01')
        doc['datiFarmaco'].append({'denominazione': 'OTHER', 'aic6': '099999'})
        scraper = scraper_for({'multi': doc}, {'multi': [IV1 + IV2.replace('044386035', '099999001')]})
        rows = asyncio.run(scraper.search('KEYTRUDA'))
        self.assertEqual([r['extra']['aic'] for r in rows], ['044386023'])

    def test_verified_search_parameters_and_pagination(self):
        scraper = object.__new__(tnf.ItAifaTnfScraper)
        first = [{'id': str(i), 'dataPubblicazione': '2020-01-01'} for i in range(20)]
        scraper._api_json = Mock(side_effect=[{'resourceList': first, 'totalElementNum': 21},
                                             {'resourceList': [{'id': 'latest', 'dataPubblicazione': '2020-03-01'}], 'totalElementNum': 21}])
        rows = scraper._search_determine('KEYTRUDA')
        self.assertEqual(rows[0]['id'], 'latest')
        self.assertEqual(len(rows), 21)
        args, kwargs = scraper._api_json.call_args_list[0]
        self.assertEqual(args, ('ricerca/',))
        self.assertEqual(kwargs, dict(parola='KEYTRUDA', pageNum=0, pageSize=20,
                                      sortColumn='dataPubblicazione', sortOrder='desc'))
        self.assertEqual(scraper._api_json.call_args_list[1].kwargs['pageNum'], 1)

    def test_search_transport_error_propagates(self):
        scraper = object.__new__(tnf.ItAifaTnfScraper)
        scraper._api_json = Mock(side_effect=RuntimeError('network'))
        with self.assertRaisesRegex(RuntimeError, 'network'):
            scraper._search_determine('KEYTRUDA')


if __name__ == '__main__':
    unittest.main()
