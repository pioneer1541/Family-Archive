import CatDocsView from '@src/components/cats/CatDocsView';

export default function CatDocsPage({params}: {params: {catId: string}}) {
  return <CatDocsView catId={params.catId} />;
}
